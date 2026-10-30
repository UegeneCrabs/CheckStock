import asyncio
import hashlib
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, time, timedelta
from time import monotonic

import httpx
from pydantic import TypeAdapter, ValidationError

from app.application.ozon_limits import OzonLimitsService, OzonQuotaExceeded
from app.core.constants import (
    HTTP,
    OZON_ADVERTISING,
    OZON_API_LIMITS,
    TIME,
    AdvertisingError,
    HTTPMethod,
    HTTPStatus,
    OzonReservationState,
)
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
from app.dto.ozon_limits import (
    OzonRateFeedback,
    PerformanceCompletion,
    PerformanceIdentity,
    PerformanceLease,
    PerformanceQuotaRequest,
)
from app.infrastructure.async_advertising_repository import AsyncAdvertisingRepository
from app.infrastructure.async_http import AsyncHttp, retry_after
from app.ozon.performance_limits import account_export_limit
from app.ozon.performance_limits import export_units as estimate_export_units


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
    if response.status_code == HTTPStatus.UNAUTHORIZED:
        return AdvertisingError.AUTHENTICATION
    if response.status_code == HTTPStatus.FORBIDDEN:
        return AdvertisingError.PERMISSION
    if response.status_code == HTTPStatus.NOT_FOUND:
        try:
            message = PerformanceErrorResponse.model_validate_json(response.content).error.strip().casefold()
        except ValidationError:
            return AdvertisingError.HTTP
        if message == OZON_ADVERTISING.missing_campaign_message:
            return AdvertisingError.CAMPAIGN_NOT_FOUND
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
        *,
        limits: OzonLimitsService,
        identity: PerformanceIdentity,
    ):
        self.http = http
        self.repository = repository
        self.store = store
        self.credentials = credentials
        self._token = ""
        self._expires_at = 0.0
        self.limits = limits
        self.identity = identity
        self._quota_campaigns: tuple[PerformanceCampaign, ...] | None = None
        self._lease: PerformanceLease | None = None
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
        billable = generation or path == OZON_ADVERTISING.daily_path
        if billable and (until := await self.repository.blocked_until(self.store)):
            if until > datetime.now(UTC):
                raise PerformanceApiFailure(AdvertisingError.DAILY_LIMIT)
        if billable and self._lease is None:
            async with self._report_slot(request_key(report) if report is not None else None):
                return await self._request(
                    method,
                    path,
                    params=params,
                    body=body,
                    generation=generation,
                    authenticated=authenticated,
                    report=report,
                )
        export_units = 0
        account_limit = OZON_API_LIMITS.performance_exports_per_day
        if billable:
            if report is None:
                raise ValueError("Performance report metadata is required")
            if self._quota_campaigns is None:
                self._quota_campaigns = await self.campaigns(report.period, fresh=True)
                if any(item.state is None for item in self._quota_campaigns):
                    raise PerformanceApiFailure(AdvertisingError.INVALID_RESPONSE)
            account_limit = account_export_limit(self._quota_campaigns)
            export_units = estimate_export_units(report, self._quota_campaigns)
        refreshed = False
        for attempt in range(OZON_ADVERTISING.attempts):
            if billable and (until := await self.repository.blocked_until(self.store)):
                if until > datetime.now(UTC):
                    raise PerformanceApiFailure(AdvertisingError.DAILY_LIMIT)
            if authenticated and monotonic() >= self._expires_at:
                await self._authorize()
            await self.limits.wait_performance(self.identity)
            if self._lease is not None:
                await self.limits.renew(self._lease)
            try:
                reservation = await self.limits.reserve_performance(
                    PerformanceQuotaRequest(
                        identity=self.identity,
                        export_units=export_units,
                        account_export_limit=account_limit,
                        request_key=request_key(report) if report is not None and billable else None,
                        generation=generation,
                        http_units=int(path != OZON_ADVERTISING.products_path),
                    )
                )
            except OzonQuotaExceeded as quota_error:
                raise PerformanceApiFailure(quota_error.code) from None
            event = PerformanceRequestEvent(
                request_id=reservation.id,
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
            except asyncio.CancelledError:
                await self.limits.finish_performance(
                    PerformanceCompletion(reservation=reservation, state=OzonReservationState.UNCERTAIN)
                )
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                await self.limits.finish_performance(
                    PerformanceCompletion(reservation=reservation, state=OzonReservationState.UNCERTAIN)
                )
                await self.repository.record_request(
                    event.model_copy(update={"error": AdvertisingError.NETWORK})
                )
                if billable or attempt == OZON_ADVERTISING.attempts - 1:
                    raise PerformanceApiFailure(AdvertisingError.NETWORK) from None
                await asyncio.sleep(OZON_ADVERTISING.backoff_seconds * (attempt + 1))
                continue
            error = response_error(response)
            state = (
                OzonReservationState.ACCEPTED
                if error is None and generation
                else OzonReservationState.RESOLVED
                if error is None
                else OzonReservationState.REJECTED
                if response.status_code < HTTPStatus.INTERNAL_SERVER_ERROR
                else OzonReservationState.UNCERTAIN
            )
            await self.limits.finish_performance(
                PerformanceCompletion(reservation=reservation, status=response.status_code, state=state)
            )
            pause = retry_after(response.headers.get(OZON_API_LIMITS.retry_header))
            try:
                remaining = int(response.headers[OZON_API_LIMITS.remaining_header])
            except (KeyError, ValueError):
                remaining = None
            await self.limits.observe_performance(
                self.identity,
                OzonRateFeedback(status=response.status_code, retry_seconds=pause, remaining=remaining),
            )
            await self.repository.record_request(
                event.model_copy(
                    update={
                        "status_code": response.status_code,
                        "error": error,
                        "export_units": reservation.export_units if error is None else 0,
                    }
                )
            )
            if error is None:
                if generation:
                    try:
                        ReportTicket.model_validate_json(response.content)
                    except ValidationError:
                        await self.limits.finish_performance(
                            PerformanceCompletion(
                                reservation=reservation, state=OzonReservationState.UNCERTAIN
                            )
                        )
                        raise PerformanceApiFailure(AdvertisingError.INVALID_RESPONSE) from None
                return response.content
            if error == AdvertisingError.DAILY_LIMIT:
                until = datetime.now(UTC) + timedelta(
                    seconds=pause if pause is not None and pause > 0 else OZON_ADVERTISING.quota_pause_seconds
                )
                affected = tuple(
                    account.store_slug
                    for account in self.identity.accounts
                    if account.organization_scope == self.identity.organization_scope
                ) or (self.store,)
                for store in affected:
                    await self.repository.block_exports(store, until)
                raise PerformanceApiFailure(error, response.status_code)
            if authenticated and response.status_code == HTTPStatus.UNAUTHORIZED and not refreshed:
                self._expires_at = 0
                refreshed = True
                continue
            retryable = response.status_code == HTTPStatus.TOO_MANY_REQUESTS or (
                not billable and response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
            )
            if not retryable or attempt == OZON_ADVERTISING.attempts - 1:
                raise PerformanceApiFailure(error, response.status_code)
            await asyncio.sleep(
                pause if pause is not None else OZON_ADVERTISING.backoff_seconds * (attempt + 1)
            )
        raise PerformanceApiFailure(AdvertisingError.ATTEMPTS_EXHAUSTED)

    async def campaigns(
        self, period: AdvertisingPeriod, *, fresh: bool = False
    ) -> tuple[PerformanceCampaign, ...]:
        request = PerformanceReportRequest(
            refresh_slot=self.refresh_slot,
            method=HTTPMethod.GET,
            path=OZON_ADVERTISING.campaigns_path,
            period=period,
        )
        key = request_key(request)
        if not fresh and (cached := await self.repository.response(self.store, key)) is not None:
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
        if (cached := await self._saved_response(request, key)) is not None:
            return DailyCampaignReport.model_validate_json(cached)
        async with self._report_slot(key):
            if (cached := await self._saved_response(request, key)) is not None:
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

    @asynccontextmanager
    async def _report_slot(self, current_key: str | None) -> AsyncIterator[None]:
        async with self.limits.report(self.identity) as lease:
            self._lease = lease
            try:
                await self._finish_pending(current_key)
                yield
            finally:
                self._lease = None

    async def _finish_pending(self, current_key: str | None) -> None:
        stores = tuple(
            account.store_slug
            for account in self.identity.accounts
            if account.account_scope == self.identity.account_scope
        ) or (self.store,)
        for pending in await self.repository.pending_reports(stores):
            if pending.request_key == current_key and pending.store_slug == self.store:
                continue
            deadline = monotonic() + OZON_ADVERTISING.report_timeout_seconds
            poll_delay = OZON_ADVERTISING.poll_seconds
            while monotonic() < deadline:
                try:
                    raw = await self._request(
                        HTTPMethod.GET, OZON_ADVERTISING.status_path.format(uuid=pending.uuid)
                    )
                except PerformanceApiFailure as error:
                    if error.status_code == HTTPStatus.NOT_FOUND:
                        await self.repository.forget_report(pending.store_slug, pending.request_key)
                        await self.limits.resolve_report(self.identity, pending.request_key)
                        break
                    raise
                state = ReportStatus.model_validate_json(raw).state
                if state == OZON_ADVERTISING.ready_report:
                    await self.repository.record_report(
                        pending.store_slug, pending.request_key, pending.uuid, pending=False
                    )
                    await self.limits.resolve_report(self.identity, pending.request_key)
                    break
                if state not in OZON_ADVERTISING.pending_reports:
                    await self.repository.forget_report(pending.store_slug, pending.request_key)
                    await self.limits.resolve_report(self.identity, pending.request_key)
                    break
                await asyncio.sleep(poll_delay)
                poll_delay = min(
                    poll_delay * HTTP.retry_backoff_multiplier, OZON_ADVERTISING.max_poll_seconds
                )
            else:
                raise PerformanceApiFailure(AdvertisingError.REPORT_TIMEOUT)

    async def _report(self, request: PerformanceReportRequest) -> bytes:
        key = request_key(request)
        if (cached := await self._saved_response(request, key)) is not None:
            return cached
        async with self._report_slot(key):
            return await self._load_report(request)

    def _account_stores(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (self.store,)
                + tuple(
                    account.store_slug
                    for account in self.identity.accounts
                    if account.account_scope == self.identity.account_scope
                )
            )
        )

    async def _saved_response(self, request: PerformanceReportRequest, key: str) -> bytes | None:
        for store in self._account_stores():
            cached = await self.repository.response(store, key)
            if cached is not None:
                if store != self.store:
                    await self.repository.save_response(self.store, key, request, cached)
                return cached
        return None

    async def _load_report(self, request: PerformanceReportRequest) -> bytes:
        key = request_key(request)
        if (cached := await self._saved_response(request, key)) is not None:
            return cached
        saved = await self.repository.report(self.store, key)
        if saved is None:
            for store in self._account_stores():
                if store == self.store:
                    continue
                saved = await self.repository.report(store, key)
                if saved is not None:
                    await self.repository.record_report(self.store, key, saved.uuid, pending=saved.pending)
                    break
        uuid = saved.uuid if saved else None
        if saved is not None:
            await self.limits.resolve_report(self.identity, key)
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
            await self.limits.resolve_report(self.identity, key)
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
                    for store in self._account_stores():
                        if store != self.store:
                            alias = await self.repository.report(store, key)
                            if alias is not None and alias.uuid == uuid:
                                await self.repository.record_report(store, key, uuid, pending=False)
                    return result
            except PerformanceApiFailure as error:
                if error.status_code == HTTPStatus.NOT_FOUND:
                    await self.repository.forget_report(self.store, key)
                    await self.limits.resolve_report(self.identity, key)
                raise
            if state not in OZON_ADVERTISING.pending_reports:
                await self.repository.forget_report(self.store, key)
                await self.limits.resolve_report(self.identity, key)
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
