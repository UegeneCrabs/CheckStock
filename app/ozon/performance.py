"""Read-only Ozon Performance API access for product advertising reports."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api-performance.ozon.ru"
TIMEOUT_SECONDS = 35


class PerformanceApiError(Exception):
    pass


def _request(
    method: str, path: str, *, token: str = "", payload: dict | None = None,
    params: dict | None = None, accept: str = "application/json",
) -> tuple[bytes, str]:
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": accept, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        detail = (
            "ошибка авторизации"
            if path == "/api/client/token"
            else error.read().decode("utf-8", errors="replace")[:400]
        )
        raise PerformanceApiError(f"Performance API {path}: HTTP {error.code}: {detail}") from error
    except (TimeoutError, urllib.error.URLError) as error:
        raise PerformanceApiError(f"Performance API {path}: {error}") from error


def _json(method: str, path: str, **kwargs) -> dict:
    raw, _ = _request(method, path, **kwargs)
    try:
        result = json.loads(raw)
    except ValueError as error:
        raise PerformanceApiError(f"Performance API {path}: ответ не в JSON") from error
    if not isinstance(result, dict):
        raise PerformanceApiError(f"Performance API {path}: неожиданный формат ответа")
    return result


def authorize(client_id: str, client_secret: str) -> str:
    response = _json(
        "POST", "/api/client/token",
        payload={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
    )
    token = str(response.get("access_token") or "")
    if not token:
        raise PerformanceApiError("Performance API не вернул токен")
    return token


def list_campaigns(token: str) -> list[dict]:
    campaigns: list[dict] = []
    page_size = 100
    for page in range(1, 101):
        result = _json(
            "GET", "/api/client/campaign", token=token,
            params={"page": page, "pageSize": page_size, "advObjectType": "SKU"},
        )
        batch = result.get("list") or []
        if isinstance(batch, dict):
            batch = [batch]
        if not isinstance(batch, list):
            raise PerformanceApiError("Performance API вернул неверный список кампаний")
        campaigns.extend(batch)
        if len(batch) < page_size:
            return campaigns
    raise PerformanceApiError("Performance API вернул более 10 000 кампаний")


def report(
    token: str, method: str, path: str, *, payload: dict | None = None, params: dict | None = None,
) -> bytes:
    created = _json(method, path, token=token, payload=payload, params=params)
    request_id = str(created.get("UUID") or created.get("uuid") or "")
    if not request_id:
        raise PerformanceApiError(f"Performance API {path}: нет идентификатора отчёта")
    for _ in range(60):
        status = _json("GET", "/api/client/statistics/" + request_id, token=token)
        state = str(status.get("state") or "")
        if state == "OK":
            raw, _ = _request(
                "GET", "/api/client/statistics/report", token=token,
                params={"UUID": request_id}, accept="*/*",
            )
            return raw
        if state == "ERROR":
            raise PerformanceApiError(str(status.get("error") or "Ozon не сформировал отчёт"))
        time.sleep(2)
    raise PerformanceApiError("Ozon не сформировал рекламный отчёт за 2 минуты")
