import json
import time
from collections.abc import Callable
from typing import cast
from urllib.parse import quote

import httpx
from google.auth import jwt
from pydantic import BaseModel

from app.core.constants import GOOGLE_WEEK, WeekIssue
from app.dto.google_week import CellWrite, GoogleDocument, GridRange, WeekError
from app.ff_import.google_service_account import get_credentials
from app.infrastructure.async_http import AsyncHttp


class TokenResponse(BaseModel):
    access_token: str
    expires_in: int = GOOGLE_WEEK.token_seconds


def value_batches(title: str, writes: list[CellWrite]) -> list[list[CellWrite]]:
    quoted = "'" + title.replace("'", "''") + "'"
    overhead = len(json.dumps({"valueInputOption": "RAW", "data": []}).encode())
    batches: list[list[CellWrite]] = []
    batch: list[CellWrite] = []
    size = overhead
    for entry in writes:
        entry_size = len(json.dumps({"range": f"{quoted}!{entry.cell}", "values": [[entry.value]]}).encode())
        if overhead + entry_size > GOOGLE_WEEK.max_payload_bytes:
            raise WeekError(WeekIssue.TOO_LARGE, cell=entry.cell)
        separator_size = len(", ") if batch else 0
        if size + separator_size + entry_size > GOOGLE_WEEK.max_payload_bytes:
            batches.append(batch)
            batch = []
            size = overhead
            separator_size = 0
        batch.append(entry)
        size += separator_size + entry_size
    if batch:
        batches.append(batch)
    return batches


class GoogleWeekClient:
    def __init__(self, http: AsyncHttp):
        self.http = http
        self.tokens: dict[bool, tuple[str, float]] = {}

    async def _token(self, *, read_only: bool) -> str:
        cached = self.tokens.get(read_only)
        if cached and cached[1] > time.time() + GOOGLE_WEEK.poll_seconds:
            return cached[0]
        try:
            credentials = get_credentials()
            stamp = int(time.time())
            encode_jwt = cast(Callable[[object, dict[str, str | int]], bytes], jwt.encode)
            assertion = encode_jwt(
                credentials.signer,
                {
                    "iss": credentials.service_account_email,
                    "scope": GOOGLE_WEEK.scope + (".readonly" if read_only else ""),
                    "aud": GOOGLE_WEEK.token_url,
                    "iat": stamp,
                    "exp": stamp + GOOGLE_WEEK.token_seconds,
                },
            ).decode("ascii")
        except Exception as error:
            raise WeekError(WeekIssue.CREDENTIALS) from error
        response = await self.http.client.post(
            GOOGLE_WEEK.token_url,
            data={"grant_type": GOOGLE_WEEK.grant_type, "assertion": assertion},
            timeout=GOOGLE_WEEK.timeout_seconds,
        )
        if response.is_error:
            raise WeekError(WeekIssue.GOOGLE, status=response.status_code)
        token = TokenResponse.model_validate(response.json())
        self.tokens[read_only] = (token.access_token, time.time() + token.expires_in)
        return token.access_token

    async def request(
        self, method: str, path: str, *, body: object = None, params: list[tuple[str, str]] | None = None
    ) -> httpx.Response:
        try:
            token = await self._token(read_only=method == "GET")
            response = await self.http.client.request(
                method,
                f"{GOOGLE_WEEK.api_url}/{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=tuple(params) if params is not None else None,
                json=body,
                timeout=GOOGLE_WEEK.timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise WeekError(WeekIssue.GOOGLE, detail=type(error).__name__) from error
        if response.is_error:
            raise WeekError(WeekIssue.GOOGLE, status=response.status_code)
        return response

    async def metadata(self, doc_id: str) -> GoogleDocument:
        response = await self.request(
            "GET",
            doc_id,
            params=[
                (
                    "fields",
                    "sheets(properties(sheetId,title,sheetType,gridProperties),merges,protectedRanges)",
                )
            ],
        )
        return GoogleDocument.model_validate(response.json())

    async def read(self, doc_id: str, titles: list[str]) -> GoogleDocument:
        params = [("fields", GOOGLE_WEEK.grid_fields), ("includeGridData", "true")]
        params.extend(("ranges", "'" + title.replace("'", "''") + "'") for title in titles)
        response = await self.request("GET", doc_id, params=params)
        return GoogleDocument.model_validate(response.json())

    async def write(self, doc_id: str, title: str, writes: list[CellWrite]) -> None:
        quoted = "'" + title.replace("'", "''") + "'"
        for batch in value_batches(title, writes):
            body = {
                "valueInputOption": "RAW",
                "data": [{"range": f"{quoted}!{entry.cell}", "values": [[entry.value]]} for entry in batch],
            }
            await self.request("POST", f"{quote(doc_id, safe='')}/values:batchUpdate", body=body)

    async def freeze(self, doc_id: str, ranges: list[GridRange]) -> None:
        requests = []
        for region in ranges:
            grid = region.model_dump(exclude_none=True)
            requests.append(
                {
                    "copyPaste": {
                        "source": grid,
                        "destination": grid,
                        "pasteType": "PASTE_VALUES",
                        "pasteOrientation": "NORMAL",
                    }
                }
            )
        await self.request("POST", f"{quote(doc_id, safe='')}:batchUpdate", body={"requests": requests})

    async def batch_update(self, doc_id: str, requests: list[dict[str, object]]) -> None:
        batches: list[list[dict[str, object]]] = []
        batch: list[dict[str, object]] = []
        size = 0
        for request in requests:
            length = len(json.dumps(request).encode())
            if length > GOOGLE_WEEK.max_payload_bytes:
                raise WeekError(WeekIssue.TOO_LARGE)
            if batch and size + length > GOOGLE_WEEK.max_payload_bytes:
                batches.append(batch)
                batch, size = [], 0
            batch.append(request)
            size += length
        if batch:
            batches.append(batch)
        for batch in batches:
            await self.request("POST", f"{quote(doc_id, safe='')}:batchUpdate", body={"requests": batch})
