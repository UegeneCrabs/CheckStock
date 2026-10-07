"""Financial provider adapter, persistent report tickets and shared API quotas."""

import hashlib
import json
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC, datetime, timedelta
from io import BytesIO

from app.config import settings
from app.jobs import locks
from app.repositories import yandex_report_limits as limits
from app.yandex import api, tokens
from app.yandex.finance_mapping import PARSERS, FinanceSourceError

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_UNPACKED_BYTES = 512 * 1024 * 1024


class FinanceSecrets:
    """Per-connection files; old token configuration is only a read-only fallback."""

    def __init__(self, directory=None):
        self.directory = (
            directory
            or settings.yandex_finance_secrets_path
            or settings.yandex_tokens_path.parent / "yandex-finance"
        )

    def _path(self, identifier):
        from uuid import UUID

        return self.directory / f"{UUID(identifier)}.json"

    def set(self, identifier, key):
        if not key.strip():
            raise ValueError("Пустой ключ")
        path = self._path(identifier)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"api_key": key.strip()}), encoding="utf-8")
        temporary.replace(path)

    def get(self, connection):
        path = self._path(connection["id"])
        if path.exists():
            key = json.loads(path.read_text(encoding="utf-8")).get("api_key")
            if key:
                return key
        return tokens.get_api_key(connection["store_slug"])


def download_archive(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise FinanceSourceError("Маркет не вернул HTTPS-ссылку на архив")
    with urllib.request.urlopen(url, timeout=settings.rnp_report_download_timeout_seconds) as response:
        body = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(body) > MAX_ARCHIVE_BYTES:
        raise FinanceSourceError("Архив слишком велик; требуется разбивка подключения по кампаниям")
    result = {}
    with zipfile.ZipFile(BytesIO(body)) as archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_UNPACKED_BYTES:
            raise FinanceSourceError("Распакованный отчёт превышает допустимый размер")
        for item in archive.infolist():
            if item.is_dir():
                continue
            name = item.filename.rsplit("/", 1)[-1]
            if not name.endswith(".json"):
                raise FinanceSourceError("Неизвестный формат файла финансового архива")
            sheet = name[:-5]
            if sheet in result:
                raise FinanceSourceError("Архив содержит повтор листа")
            result[sheet] = json.loads(archive.read(item).decode("utf-8-sig"))
    if not result:
        raise FinanceSourceError("Маркет вернул пустой архив")
    return result


class FinanceProvider:
    def __init__(self, repository, secrets=None):
        self.repository = repository
        self.secrets = secrets or FinanceSecrets()
        self.tickets = {}

    def report(self, connection, report, payload):
        business = connection["business_id"]
        key = hashlib.sha256(
            json.dumps([connection["id"], report, payload], sort_keys=True).encode()
        ).hexdigest()
        token = self.secrets.get(connection)
        # Share the same process-independent mutex and deadline table as economics.
        deadline = time.monotonic() + 15 * 60
        while time.monotonic() < deadline:
            try:
                handle = locks.acquire(f"yandex-reports:{business}")
            except locks.SyncJobBusyError:
                time.sleep(1)
                continue
            try:
                report_id = self.repository.ticket(key)
                wait = limits.remaining(business, report) if not report_id else 0
                if wait <= 0:
                    if not report_id:
                        limits.defer(business, report, datetime.now(UTC) + timedelta(seconds=125))
                        try:
                            params = {"format": "JSON"}
                            if report != "goods-realization":
                                params["language"] = "RU"
                            generated = api.request(
                                f"/v2/reports/{report}/generate", token, payload=payload, params=params
                            )
                        except api.YandexApiError as error:
                            if error.status in (420, 429):
                                limits.defer(
                                    business,
                                    report,
                                    max(
                                        error.retry_at or datetime.now(UTC),
                                        datetime.now(UTC) + timedelta(seconds=250),
                                    ),
                                )
                            raise FinanceSourceError(
                                f"Маркет: генерация {report}, ошибка {error.status or 'сети'}. Повторите задание"
                            ) from None
                        report_id = str(generated.get("reportId") or "")
                        if not report_id:
                            raise FinanceSourceError("Маркет не вернул ID отчёта")
                        self.repository.ticket(key, report_id)
                    self.tickets[report_id] = key
                    break
            finally:
                handle.close()
            time.sleep(min(max(wait, 1), 30))
        else:
            raise FinanceSourceError("Квота или блокировка кабинета занята; повторите задание позже")
        for _ in range(121):
            try:
                info = api.request(f"/v2/reports/info/{report_id}", token, method="GET")
            except api.YandexApiError as error:
                if error.status == 404:
                    self.repository.ticket(key, "")
                    raise FinanceSourceError(
                        "Срок хранения отчёта в Маркете истёк. Повтор создаст новый отчёт"
                    ) from None
                raise FinanceSourceError(f"Маркет: опрос отчёта, ошибка {error.status or 'сети'}") from None
            status = info.get("status")
            if status == "DONE":
                return download_archive(str(info.get("file") or "")), report_id
            if status == "FAILED":
                self.repository.ticket(key, "")
                raise FinanceSourceError("Маркет не сформировал финансовый отчёт; повторите задание")
            if status not in {"PENDING", "PROCESSING"}:
                raise FinanceSourceError("Неизвестное состояние генерации отчёта")
            time.sleep(5)
        raise FinanceSourceError("Отчёт ещё формируется. Повтор продолжит опрос сохранённого ID")

    def business_orders(self, connection, start, end):
        token = self.secrets.get(connection)
        result, seen = [], set()
        current = start
        while current <= end:
            # Inclusive API dates, no more than 30 days per request.
            last = min(current + timedelta(days=29), end)
            page, seen_pages = "", set()
            while True:
                value = api.request(
                    f"/v1/businesses/{connection['business_id']}/orders",
                    token,
                    payload={
                        "campaignIds": connection["campaign_ids"],
                        "dates": {"creationDateFrom": str(current), "creationDateTo": str(last)},
                        "fake": False,
                        "sourcePlatforms": ["MARKET"],
                    },
                    params={"limit": 50, "pageToken": page},
                )
                rows = value.get("orders")
                if not isinstance(rows, list):
                    raise FinanceSourceError("Маркет не вернул список заказов")
                for row in rows:
                    identifier = (row.get("campaignId"), row.get("orderId"))
                    if not all(identifier) or identifier in seen:
                        raise FinanceSourceError("Повтор заказа или отсутствующий ID в пагинации")
                    seen.add(identifier)
                    result.append(row)
                page = (value.get("paging") or {}).get("nextPageToken") or ""
                if not page:
                    break
                if page in seen_pages or not rows or len(result) > 500_000:
                    raise FinanceSourceError("Неполная или циклическая пагинация заказов")
                seen_pages.add(page)
            current = last + timedelta(days=1)
        return {"orders": result}

    def load(self, connection, source, start, end):
        ids = []
        payload = {
            "businessId": connection["business_id"],
            "campaignIds": connection["campaign_ids"],
            "dateFrom": str(start),
            "dateTo": str(end),
        }
        if source == "realization":
            raw = {"campaigns": {}}
            for cid in connection["campaign_ids"]:
                raw["campaigns"][str(cid)], rid = self.report(
                    connection,
                    "goods-realization",
                    {"campaignId": cid, "year": start.year, "month": start.month},
                )
                ids.append(rid)
            raw["operational"], rid = self.report(connection, "united-orders", payload)
            ids.append(rid)
        elif source == "orders":
            raw = self.business_orders(connection, start, end)
        else:
            name = {"services": "united-marketplace-services", "payments": "united-netting"}[source]
            raw, rid = self.report(connection, name, payload)
            ids.append(rid)
        return PARSERS[source](connection, raw, start, end).model_copy(update={"report_ids": tuple(ids)})

    def reparse(self, connection, batch):
        if not batch.raw:
            raise FinanceSourceError("Исходный архив отсутствует; требуется повторная загрузка")
        return PARSERS[batch.source](connection, batch.raw, batch.start, batch.end).model_copy(
            update={"report_ids": batch.report_ids}
        )

    def published(self, connection, batches):
        for batch in batches:
            for identifier in batch.report_ids:
                key = self.tickets.pop(identifier, None)
                if key:
                    self.repository.ticket(key, "")

    def discover(self, store, key=""):
        token = key or tokens.get_api_key(store)
        campaigns = [api.normalize_campaign(row) for row in api.get_campaigns(token)]
        return {
            "campaigns": campaigns,
            "requires_business_selection": len({r["business_id"] for r in campaigns}) != 1,
            "finance_permissions_verified": False,
        }
