import asyncio
from collections import defaultdict
from datetime import date, timedelta
from urllib.parse import urlsplit

from app.config import settings
from app.core.constants import OZON_STORAGE, HTTPMethod, HTTPStatus
from app.dto.ozon_storage import (
    PlacementCode,
    PlacementInfo,
    PlacementInfoPage,
    PlacementRequest,
    ReportRequest,
    StorageAccrualPage,
    StorageAccrualRequest,
    StorageArticle,
    StorageEvidence,
    StoragePeriod,
    StoragePostingPage,
    StoragePostingRequest,
    StorageReport,
    StorageSale,
)
from app.ozon.async_api import AsyncSellerApi
from app.ozon.storage_normalizer import placement_report


class StorageReportPendingError(RuntimeError):
    pass


class StorageReportFailedError(RuntimeError):
    pass


class AsyncStorageClient(AsyncSellerApi):
    async def create(self, period: StoragePeriod) -> str:
        response = await self._response(
            OZON_STORAGE.create_path, PlacementRequest(date_from=period.start, date_to=period.end)
        )
        return PlacementCode.model_validate(response.json()).code

    async def info(self, code: str) -> PlacementInfo:
        response = await self._response(OZON_STORAGE.info_path, ReportRequest(code=code))
        info = PlacementInfoPage.model_validate(response.json()).result
        if info.report_type.lower() != OZON_STORAGE.report_type:
            raise ValueError("Unexpected Ozon storage report type")
        return info

    async def report(self, code: str, period: StoragePeriod) -> StorageReport:
        for attempt in range(OZON_STORAGE.poll_attempts):
            info = await self.info(code)
            if info.status == OZON_STORAGE.failed_status:
                raise StorageReportFailedError("Ozon storage report failed")
            if info.status == OZON_STORAGE.success_status:
                url = urlsplit(info.file)
                host = url.hostname or ""
                if (
                    url.scheme != "https"
                    or url.username
                    or url.password
                    or url.port not in (None, 443)
                    or not any(
                        host == domain or host.endswith("." + domain)
                        for domain in OZON_STORAGE.download_domains
                    )
                ):
                    raise ValueError("Unexpected Ozon storage download address")
                content = bytearray()
                async with self.http.client.stream(
                    HTTPMethod.GET, info.file, follow_redirects=False
                ) as response:
                    if response.status_code != HTTPStatus.OK:
                        raise RuntimeError("Storage report download failed")
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > OZON_STORAGE.max_download_bytes:
                            raise ValueError("Storage report download limit exceeded")
                return placement_report(bytes(content), period)
            if info.status not in OZON_STORAGE.pending_statuses:
                raise ValueError("Unknown Ozon storage report status")
            if attempt + 1 < OZON_STORAGE.poll_attempts:
                await asyncio.sleep(OZON_STORAGE.poll_seconds)
        raise StorageReportPendingError("Ozon storage report is still processing")

    async def sales(self, period: StoragePeriod) -> tuple[StorageSale, ...]:
        sold: dict[tuple[date, str], int] = defaultdict(int)
        returned: dict[tuple[date, str], int] = defaultdict(int)
        postings: dict[tuple[date, str], str] = {}
        seen: set[str] = set()
        for offset in range(OZON_STORAGE.window_days):
            day = period.start + timedelta(days=offset)
            cursor = ""
            cursors: set[str] = set()
            for _ in range(OZON_STORAGE.max_accrual_pages):
                response = await self._response(
                    OZON_STORAGE.accrual_path, StorageAccrualRequest(date=day, last_id=cursor)
                )
                page = StorageAccrualPage.model_validate(response.json())
                for accrual in page.accruals:
                    if not accrual.accrual_id or accrual.accrual_id in seen or accrual.date != day:
                        raise ValueError("Repeated or invalid storage accrual")
                    seen.add(accrual.accrual_id)
                    if (
                        accrual.posting is None
                        or accrual.posting.delivery_schema.lower() != OZON_STORAGE.fbo_schema
                    ):
                        continue
                    for product in accrual.posting.products:
                        if product.commission is None:
                            continue
                        amount = product.commission.sale_amount
                        if amount.currency != OZON_STORAGE.currency or not product.sku or amount.amount == 0:
                            raise ValueError("Ambiguous storage realization quantity")
                        target = sold if amount.amount > 0 else returned
                        target[day, product.sku] += product.quantity
                        postings[day, product.sku] = accrual.unit_number
                await asyncio.sleep(settings.ozon_storage_request_interval_seconds)
                if not page.last_id:
                    break
                if page.last_id in cursors or not page.accruals:
                    raise ValueError("Incomplete Ozon storage accrual pagination")
                cursors.add(page.last_id)
                cursor = page.last_id
            else:
                raise ValueError("Ozon storage accrual page limit exceeded")
        return tuple(
            StorageSale(
                day=day,
                sku=sku,
                sold=sold[day, sku],
                returned=returned[day, sku],
                posting_number=postings[day, sku],
            )
            for day, sku in sorted(sold.keys() | returned.keys())
        )

    async def evidence(self, period: StoragePeriod, code: str) -> StorageEvidence:
        report = await self.report(code, period)
        sales = await self.sales(period)
        known_skus = {row.sku for row in report.rows}
        index = await self.product_articles(tuple(sorted({sale.sku for sale in sales} - known_skus)))
        articles = [StorageArticle(sku=item.sku, article=item.article) for item in index.items]
        known_skus.update(item.sku for item in articles)
        for sale in reversed(sales):
            if sale.sku in known_skus:
                continue
            if not sale.posting_number:
                raise ValueError("Missing posting for historical storage SKU")
            response = await self._response(
                OZON_STORAGE.posting_path, StoragePostingRequest(posting_number=sale.posting_number)
            )
            posting = StoragePostingPage.model_validate(response.json()).result
            if posting.posting_number != sale.posting_number:
                raise ValueError("Unexpected storage posting identity")
            for product in posting.products:
                if product.sku and product.offer_id.strip():
                    articles.append(StorageArticle(sku=product.sku, article=product.offer_id.strip()))
                    known_skus.add(product.sku)
            if sale.sku not in known_skus:
                raise ValueError("Unmapped historical storage SKU")
            await asyncio.sleep(settings.ozon_storage_request_interval_seconds)
        return StorageEvidence(
            period=period,
            report=report,
            sales=sales,
            articles=tuple(articles),
        )
