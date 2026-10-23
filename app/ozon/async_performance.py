import asyncio
import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime, time, timedelta
from time import monotonic

import httpx
from pydantic import TypeAdapter, ValidationError

from app.core.constants import HTTP, OZON_ADVERTISING, TIME, AdvertisingError, HTTPMethod, HTTPStatus
from app.dto.ozon_advertising import (
    AdvertisingPeriod,
    AllProductsOrdersReport,
    CampaignInventory,
    CampaignPage,
    CampaignReportEnvelope,
    DailyCampaignReport,
    PerformanceCampaign,
    PerformanceCredentials,
    PerformanceErrorResponse,
    PerformanceReportRequest,
    PerformanceRequestEvent,
    PerformanceToken,
    ProductTrafficReport,
    ReportStatus,
    ReportTicket,
    SelectedOrdersReport,
)
from app.infrastructure.async_advertising_repository import AsyncAdvertisingRepository
from app.infrastructure.async_http import AsyncHttp, retry_after


class PerformanceApiFailure(Exception):
    def __init__(self, code: AdvertisingError, status_code: int | None = None):
        self.code = code
        self.status_code = status_code
        super().__init__(code if status_code is None else f"{code}:{status_code}")


def request_key(request: PerformanceReportRequest) -> str:
    return hashlib.sha256(request.model_dump_json(exclude_none=True).encode()).hexdigest()


def response_error(response: httpx.Response) -> AdvertisingError | None:
    if response.status_code == HTTPStatus.OK:
        return None
    if response.status_code != HTTPStatus.TOO_MANY_REQUESTS:
        return AdvertisingError.HTTP
    try:
        message = PerformanceErrorResponse.model_validate_json(response.content).error.lower()
    except ValidationError:
        return AdvertisingError.RATE_LIMIT
    if any(marker in message for marker in OZON_ADVERTISING.daily_limit_markers):
        return AdvertisingError.DAILY_LIMIT
    if any(marker in message for marker in OZON_ADVERTISING.active_limit_markers):
        return AdvertisingError.ACTIVE_LIMIT
    return AdvertisingError.RATE_LIMIT


class AsyncPerformanceClient:
    def __init__(
        self,
        http: AsyncHttp,
        repository: AsyncAdvertisingRepository,
        store: str,
        credentials: PerformanceCredentials,
    ):
        self.http = http
        self.repository = repository
        self.store = store
        self.credentials = credentials
        self._token = ""
        self._expires_at = 0.0
        self._next_request = 0.0
        self.refresh_slot: str | None = None

    async def _authorize(self) -> None:
        raw = await self._request(
            HTTPMethod.POST,
            OZON_ADVERTISING.token_path,
            body={
                "client_id": self.credentials.client_id,
                "client_secret": self.credentials.client_secret,
                "grant_type": OZON_ADVERTISING.grant_type,
            },
            authenticated=False,
        )
        token = PerformanceToken.model_validate_json(raw)
        self._token = token.access_token
        self._expires_at = monotonic() + max(0, token.expires_in - OZON_ADVERTISING.token_margin_seconds)

    async def _request(
        self,
        method: HTTPMethod,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        body: object = None,
        generation: bool = False,
        authenticated: bool = True,
        report: PerformanceReportRequest | None = None,
    ) -> bytes:
        refreshed = False
        for attempt in range(OZON_ADVERTISING.attempts):
            if generation and (until := await self.repository.blocked_until(self.store)):
                if until > datetime.now(UTC):
                    raise PerformanceApiFailure(AdvertisingError.DAILY_LIMIT)
            if authenticated and monotonic() >= self._expires_at:
                await self._authorize()
            await asyncio.sleep(max(0, self._next_request - monotonic()))
            self._next_request = monotonic() + OZON_ADVERTISING.request_spacing_seconds
            event = PerformanceRequestEvent(
                store_slug=self.store,
                method=method,
                path=path,
                period=report.period if report else None,
                campaign_count=len(report.campaigns) if report else 0,
                attempted_at=datetime.now(UTC),
            )
            try:
                response = await self.http.request(
                    method,
                    OZON_ADVERTISING.base_url + path,
                    attempts=1,
                    backoff=OZON_ADVERTISING.backoff_seconds,
                    headers={"Authorization": f"Bearer {self._token}"} if authenticated else {},
                    params=params,
                    json=body,
                )
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                await self.repository.record_request(
                    event.model_copy(update={"error": AdvertisingError.NETWORK})
                )
                if generation or attempt == OZON_ADVERTISING.attempts - 1:
                    raise PerformanceApiFailure(AdvertisingError.NETWORK) from None
                await asyncio.sleep(OZON_ADVERTISING.backoff_seconds * (attempt + 1))
                continue
            error = response_error(response)
            await self.repository.record_request(
                event.model_copy(
                    update={
                        "status_code": response.status_code,
                        "error": error,
                        "export_units": max(event.campaign_count, 1) if generation and error is None else 0,
                    }
                )
            )
            if error is None:
                return response.content
            pause = retry_after(response.headers.get("Retry-After"))
            if error == AdvertisingError.DAILY_LIMIT:
                until = datetime.now(UTC) + timedelta(
                    seconds=pause if pause is not None and pause > 0 else OZON_ADVERTISING.quota_pause_seconds
                )
                await self.repository.block_exports(self.store, until)
                raise PerformanceApiFailure(error, response.status_code)
            if authenticated and response.status_code == HTTPStatus.UNAUTHORIZED and not refreshed:
                self._expires_at = 0
                refreshed = True
                continue
            retryable = response.status_code == HTTPStatus.TOO_MANY_REQUESTS or (
                not generation and response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
            )
            if not retryable or attempt == OZON_ADVERTISING.attempts - 1:
                raise PerformanceApiFailure(error, response.status_code)
            await asyncio.sleep(
                pause if pause is not None else OZON_ADVERTISING.backoff_seconds * (attempt + 1)
            )
        raise PerformanceApiFailure(AdvertisingError.ATTEMPTS_EXHAUSTED)

    async def campaigns(self, period: AdvertisingPeriod) -> tuple[PerformanceCampaign, ...]:
        request = PerformanceReportRequest(
            refresh_slot=self.refresh_slot,
            method=HTTPMethod.GET,
            path=OZON_ADVERTISING.campaigns_path,
            period=period,
        )
        key = request_key(request)
        if (cached := await self.repository.response(self.store, key)) is not None:
            return CampaignInventory.model_validate_json(cached).campaigns
        result: dict[str, PerformanceCampaign] = {}
        for page in range(1, OZON_ADVERTISING.max_pages + 1):
            raw = await self._request(
                HTTPMethod.GET,
                OZON_ADVERTISING.campaigns_path,
                params={"page": page, "pageSize": OZON_ADVERTISING.page_size},
                report=request,
            )
            batch = CampaignPage.model_validate_json(raw).campaigns
            if any(item.id in result for item in batch):
                raise PerformanceApiFailure(AdvertisingError.PAGINATION_REPEATED)
            result.update((item.id, item) for item in batch)
            if len(batch) < OZON_ADVERTISING.page_size:
                inventory = CampaignInventory(campaigns=tuple(result.values()))
                await self.repository.save_response(
                    self.store, key, request, inventory.model_dump_json().encode()
                )
                return inventory.campaigns
        raise PerformanceApiFailure(AdvertisingError.PAGINATION_LIMIT)

    async def daily(self, period: AdvertisingPeriod) -> DailyCampaignReport:
        request = PerformanceReportRequest(
            refresh_slot=self.refresh_slot,
            method=HTTPMethod.GET,
            path=OZON_ADVERTISING.daily_path,
            period=period,
        )
        key = request_key(request)
        if (cached := await self.repository.response(self.store, key)) is not None:
            return DailyCampaignReport.model_validate_json(cached)
        raw = await self._request(
            HTTPMethod.GET,
            OZON_ADVERTISING.daily_path,
            params={"dateFrom": period.start.isoformat(), "dateTo": period.end.isoformat()},
            report=request,
        )
        result = DailyCampaignReport.model_validate_json(raw)
        await self.repository.save_response(self.store, key, request, raw)
        return result

    async def saved_product_traffic(
        self, period: AdvertisingPeriod, campaigns: tuple[str, ...]
    ) -> ProductTrafficReport | None:
        request = PerformanceReportRequest(
            refresh_slot=self.refresh_slot,
            method=HTTPMethod.POST,
            path=OZON_ADVERTISING.products_path,
            period=period,
            campaigns=campaigns,
        )
        raw = await self.repository.response(self.store, request_key(request))
        return ProductTrafficReport.model_validate_json(raw) if raw is not None else None

    async def product_traffic(
        self, period: AdvertisingPeriod, campaigns: tuple[str, ...]
    ) -> ProductTrafficReport:
        request = PerformanceReportRequest(
            refresh_slot=self.refresh_slot,
            method=HTTPMethod.POST,
            path=OZON_ADVERTISING.products_path,
            period=period,
            campaigns=campaigns,
        )
        key = request_key(request)
        if (cached := await self.repository.response(self.store, key)) is not None:
            return ProductTrafficReport.model_validate_json(cached)
        raw = await self._request(
            request.method,
            request.path,
            body={
                "campaignIds": campaigns,
                "dateFrom": period.start.isoformat(),
                "dateTo": period.end.isoformat(),
            },
            report=request,
        )
        result = ProductTrafficReport.model_validate_json(raw)
        if any(row.campaign_id not in campaigns for row in result.rows):
            raise PerformanceApiFailure(AdvertisingError.REPORT_INCOMPLETE)
        if any(not period.start <= row.day <= period.end for row in result.rows):
            raise PerformanceApiFailure(AdvertisingError.DATE_OUTSIDE_PERIOD)
        await self.repository.save_response(self.store, key, request, raw)
        return result

    async def _report(self, request: PerformanceReportRequest) -> bytes:
        key = request_key(request)
        if (cached := await self.repository.response(self.store, key)) is not None:
            return cached
        saved = await self.repository.report(self.store, key)
        uuid = saved.uuid if saved else None
        if uuid is None:
            first = datetime.combine(request.period.start, time.min, TIME.moscow_timezone).isoformat()
            last = datetime.combine(request.period.end, time.max, TIME.moscow_timezone).isoformat(
                timespec="seconds"
            )
            if request.path == OZON_ADVERTISING.statistics_path:
                body: object = {
                    "campaigns": request.campaigns,
                    "dateFrom": request.period.start.isoformat(),
                    "dateTo": request.period.end.isoformat(),
                    "groupBy": OZON_ADVERTISING.date_group,
                }
                params: Mapping[str, str | int] | None = None
            elif request.method == HTTPMethod.GET:
                body = None
                params = {"timeBounds.from": first, "timeBounds.to": last}
            else:
                body, params = {"from": first, "to": last}, None
            raw = await self._request(
                request.method, request.path, body=body, params=params, generation=True, report=request
            )
            uuid = ReportTicket.model_validate_json(raw).uuid
            await self.repository.record_report(self.store, key, uuid, pending=True)
        deadline = monotonic() + OZON_ADVERTISING.report_timeout_seconds
        poll_delay = OZON_ADVERTISING.poll_seconds
        while monotonic() < deadline:
            try:
                if saved is not None and not saved.pending:
                    state = OZON_ADVERTISING.ready_report
                else:
                    raw = await self._request(
                        HTTPMethod.GET, OZON_ADVERTISING.status_path.format(uuid=uuid), report=request
                    )
                    state = ReportStatus.model_validate_json(raw).state
                if state == OZON_ADVERTISING.ready_report:
                    result = await self._request(
                        HTTPMethod.GET, OZON_ADVERTISING.download_path, params={"UUID": uuid}, report=request
                    )
                    await self.repository.save_response(self.store, key, request, result)
                    await self.repository.record_report(self.store, key, uuid, pending=False)
                    return result
            except PerformanceApiFailure as error:
                if error.status_code == HTTPStatus.NOT_FOUND:
                    await self.repository.forget_report(self.store, key)
                raise
            if state not in OZON_ADVERTISING.pending_reports:
                await self.repository.forget_report(self.store, key)
                raise PerformanceApiFailure(AdvertisingError.REPORT_FAILED)
            await asyncio.sleep(poll_delay)
            poll_delay = min(poll_delay * HTTP.retry_backoff_multiplier, OZON_ADVERTISING.max_poll_seconds)
        raise PerformanceApiFailure(AdvertisingError.REPORT_TIMEOUT)

    async def finish_previous(self, request: PerformanceReportRequest) -> None:
        key = request_key(request)
        saved = await self.repository.report(self.store, key)
        if saved and await self.repository.response(self.store, key) is None:
            await self._report(request)

    async def campaign_reports(
        self, period: AdvertisingPeriod, campaigns: tuple[str, ...]
    ) -> dict[str, CampaignReportEnvelope]:
        raw = await self._report(
            PerformanceReportRequest(
                refresh_slot=self.refresh_slot,
                method=HTTPMethod.POST,
                path=OZON_ADVERTISING.statistics_path,
                period=period,
                campaigns=campaigns,
            )
        )
        return TypeAdapter(dict[str, CampaignReportEnvelope]).validate_json(raw)

    async def has_campaign_report(self, period: AdvertisingPeriod, campaigns: tuple[str, ...]) -> bool:
        request = PerformanceReportRequest(
            refresh_slot=self.refresh_slot,
            method=HTTPMethod.POST,
            path=OZON_ADVERTISING.statistics_path,
            period=period,
            campaigns=campaigns,
        )
        key = request_key(request)
        return (
            await self.repository.response(self.store, key) is not None
            or await self.repository.report(self.store, key) is not None
        )

    async def selected_orders(self, period: AdvertisingPeriod) -> SelectedOrdersReport:
        raw = await self._report(
            PerformanceReportRequest(
                refresh_slot=self.refresh_slot,
                method=HTTPMethod.POST,
                path=OZON_ADVERTISING.selected_path,
                period=period,
            )
        )
        return SelectedOrdersReport.model_validate_json(raw)

    async def all_product_orders(self, period: AdvertisingPeriod) -> AllProductsOrdersReport:
        raw = await self._report(
            PerformanceReportRequest(
                refresh_slot=self.refresh_slot,
                method=HTTPMethod.GET,
                path=OZON_ADVERTISING.all_products_path,
                period=period,
            )
        )
        return AllProductsOrdersReport.model_validate_json(raw)
