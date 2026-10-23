import httpx
from pydantic import ValidationError

from app.config import settings
from app.core.constants import (
    OZON_API,
    HTTPMethod,
    OrderScheme,
)
from app.dto.orders import OzonCredentials
from app.dto.ozon_orders import (
    FboPostingsRequest,
    FbsPostingsRequest,
    OzonPosting,
    OzonPostingsPage,
    PostingFilter,
    PostingOptions,
)
from app.infrastructure.async_http import AsyncHttp
from app.ozon.api import OzonApiError, _parse_error_body


class AsyncSellerApi:
    def __init__(self, http: AsyncHttp, credentials: OzonCredentials):
        self.http = http
        self.headers = {
            "Client-Id": credentials.client_id,
            "Api-Key": credentials.api_key,
            "Accept": "application/json",
        }

    async def request(self, path: str, payload: FboPostingsRequest | FbsPostingsRequest) -> OzonPostingsPage:
        try:
            response = await self.http.request(
                HTTPMethod.POST,
                OZON_API.base_url + path,
                headers=self.headers,
                json=payload.model_dump(mode="json", by_alias=True, exclude_none=True),
                attempts=settings.ozon_request_attempts,
                backoff=settings.ozon_retry_backoff_seconds,
            )
        except httpx.TimeoutException as error:
            raise OzonApiError(None, "Ozon не ответил за отведённое время") from error
        except httpx.TransportError as error:
            raise OzonApiError(None, f"Сетевая ошибка Ozon: {type(error).__name__}") from error
        if response.is_error:
            raise OzonApiError(response.status_code, _parse_error_body(response.text))
        try:
            data: object = response.json()
        except ValueError as error:
            raise OzonApiError(None, "Ozon вернул не-JSON ответ") from error
        if not isinstance(data, dict):
            raise OzonApiError(None, "Ozon вернул некорректный ответ")
        result = data.get("result", data)
        page: dict[str, object]
        if isinstance(result, list):
            page = {"postings": result}
        elif isinstance(result, dict):
            page = {**result, "postings": result.get("postings", result.get("items"))}
        else:
            raise OzonApiError(None, "Ozon не вернул список отправлений")
        try:
            return OzonPostingsPage.model_validate(page)
        except ValidationError as error:
            details = "; ".join(
                f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                for item in error.errors(include_input=False, include_context=False)[
                    : OZON_API.validation_error_details_limit
                ]
            )
            raise OzonApiError(None, f"Некорректный ответ Ozon: {details}") from None

    async def postings(
        self,
        scheme: OrderScheme,
        since: str,
        to: str,
        *,
        posting_numbers: tuple[str, ...] | None = None,
        order_numbers: tuple[str, ...] | None = None,
    ) -> tuple[OzonPosting, ...]:
        if scheme not in OrderScheme:
            raise ValueError("Unknown Ozon order scheme")
        fbs = scheme == OrderScheme.FBS
        path = OZON_API.fbs_postings_v4_path if fbs else OZON_API.fbo_postings_v3_path
        page_size = OZON_API.fbs_postings_v4_page_size if fbs else OZON_API.fbo_postings_v3_page_size
        rows: list[OzonPosting] = []
        cursor = ""
        seen_cursors: set[str] = set()
        seen_postings: set[str] = set()
        while True:
            period = PostingFilter(
                since=since, to=to, posting_numbers=posting_numbers, order_numbers=order_numbers
            )
            options = PostingOptions(analytics_data=not fbs, barcodes=not fbs, financial_data=not fbs)
            payload: FboPostingsRequest | FbsPostingsRequest
            if fbs:
                payload = FbsPostingsRequest(
                    filter=period, limit=page_size, options=options, cursor=cursor or None
                )
            else:
                payload = FboPostingsRequest(
                    filter=period, limit=page_size, options=options, cursor=cursor or None
                )
            result = await self.request(path, payload)
            page = result.postings
            has_next = result.has_next
            if not isinstance(has_next, bool):
                raise OzonApiError(None, "Ozon не подтвердил завершённость списка отправлений")
            for posting in page:
                key = posting.posting_number
                if not key or key in seen_postings:
                    raise OzonApiError(None, "Ozon вернул отправление без номера или повторил отправление")
                seen_postings.add(key)
            rows.extend(page)
            if len(rows) > OZON_API.max_postings:
                raise OzonApiError(None, "Превышен предел обхода отправлений Ozon; выгрузка неполна")
            if has_next is False:
                return tuple(rows)
            if not page:
                raise OzonApiError(None, "Ozon вернул пустую промежуточную страницу отправлений")
            next_cursor = (result.cursor or "").strip()
            if not next_cursor or next_cursor == cursor or next_cursor in seen_cursors:
                raise OzonApiError(None, "Ozon сообщил о следующей странице без нового cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
