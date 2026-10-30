import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import cast
from urllib.parse import urlparse

import httpx
from google.auth import jwt
from google.oauth2 import service_account

from app.config import Settings
from app.core.constants import DATABASE_BACKUP, BackupIssue
from app.dto.database_backup import BackupError, BackupUpload, DriveBackupFile


class DatabaseBackupDrive:
    def __init__(self, client: httpx.AsyncClient, config: Settings):
        self.client, self.config = client, config
        self.token = ""
        self.expires_at = 0.0

    async def headers(self) -> dict[str, str]:
        if self.expires_at <= time.time() + DATABASE_BACKUP.token_refresh_seconds:
            try:
                load = cast(
                    Callable[..., service_account.Credentials],
                    service_account.Credentials.from_service_account_file,
                )
                credentials = load(
                    str(self.config.google_service_account_path), scopes=[DATABASE_BACKUP.scope]
                )
                stamp = int(time.time())
                encode = cast(Callable[[object, dict[str, str | int]], bytes], jwt.encode)
                assertion = encode(
                    credentials.signer,
                    {
                        "iss": credentials.service_account_email,
                        "scope": DATABASE_BACKUP.scope,
                        "aud": DATABASE_BACKUP.token_url,
                        "iat": stamp,
                        "exp": stamp + DATABASE_BACKUP.token_seconds,
                    },
                )
            except Exception as error:
                raise BackupError(BackupIssue.CREDENTIALS) from error
            try:
                response = await self.client.post(
                    DATABASE_BACKUP.token_url,
                    data={
                        "grant_type": DATABASE_BACKUP.token_grant,
                        "assertion": assertion.decode("ascii"),
                    },
                    timeout=self.config.database_backup.http_timeout_seconds,
                )
                if response.is_error:
                    raise BackupError(BackupIssue.CREDENTIALS, response.status_code)
                value = response.json()
                self.token = str(value["access_token"])
                self.expires_at = time.time() + int(value.get("expires_in", DATABASE_BACKUP.token_seconds))
            except httpx.HTTPError as error:
                raise BackupError(BackupIssue.CREDENTIALS) from error
        return {"Authorization": "Bearer " + self.token}

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        payload: object = None,
        retry: bool = True,
    ) -> httpx.Response:
        delays = DATABASE_BACKUP.retry_seconds if retry else ()
        for attempt in range(len(delays) + 1):
            try:
                response = await self.client.request(
                    method,
                    url,
                    params=params,
                    content=data,
                    json=payload,
                    headers={**await self.headers(), **(headers or {})},
                    timeout=self.config.database_backup.http_timeout_seconds,
                )
                if response.status_code == 401 and attempt < len(delays):
                    self.expires_at = 0
                elif response.status_code not in (429, 500, 502, 503, 504):
                    return response
            except httpx.HTTPError:
                pass
            if attempt < len(delays):
                await asyncio.sleep(delays[attempt])
        raise BackupError(BackupIssue.GOOGLE)

    async def check_folder(self) -> None:
        folder = self.config.database_backup.folder_id
        if not folder:
            raise BackupError(BackupIssue.CONFIGURATION)
        response = await self.request(
            "GET",
            DATABASE_BACKUP.api_url + "/files/" + folder,
            params={
                "supportsAllDrives": "true",
                "fields": "id,mimeType,trashed,driveId,capabilities(canAddChildren)",
            },
        )
        if response.is_error:
            raise BackupError(BackupIssue.FOLDER, response.status_code)
        data = response.json()
        if (
            data.get("mimeType") != DATABASE_BACKUP.folder_mime
            or data.get("trashed")
            or not data.get("driveId")
            or not data.get("capabilities", {}).get("canAddChildren")
        ):
            raise BackupError(BackupIssue.FOLDER)

    async def remote(self, file_id: str) -> DriveBackupFile | None:
        response = await self.request(
            "GET",
            DATABASE_BACKUP.api_url + "/files/" + file_id,
            params={
                "supportsAllDrives": "true",
                "fields": DATABASE_BACKUP.file_fields,
            },
        )
        if response.status_code == 404:
            return None
        if response.is_error:
            raise BackupError(BackupIssue.GOOGLE, response.status_code)
        return DriveBackupFile.model_validate(response.json())

    def verify(self, remote: DriveBackupFile, upload: BackupUpload) -> DriveBackupFile:
        if remote.size != upload.artifact.size or remote.md5 != upload.artifact.md5:
            raise BackupError(BackupIssue.INTEGRITY)
        return remote

    async def start(
        self, upload: BackupUpload, save: Callable[[BackupUpload], Awaitable[None]]
    ) -> BackupUpload:
        if not upload.file_id:
            response = await self.request(
                "GET", DATABASE_BACKUP.api_url + "/files/generateIds", params={"count": 1, "space": "drive"}
            )
            if response.is_error:
                raise BackupError(BackupIssue.GOOGLE, response.status_code)
            upload = upload.model_copy(update={"file_id": response.json()["ids"][0]})
            await save(upload)
        response = await self.request(
            "POST",
            DATABASE_BACKUP.upload_url,
            params={
                "uploadType": "resumable",
                "supportsAllDrives": "true",
                "fields": DATABASE_BACKUP.file_fields,
            },
            headers={
                "X-Upload-Content-Length": str(upload.artifact.size),
                "X-Upload-Content-Type": "application/octet-stream",
            },
            payload={
                "id": upload.file_id,
                "name": upload.artifact.path.name,
                "parents": [upload.folder_id],
                "description": "CheckStock database backup " + upload.artifact.day.isoformat(),
                "appProperties": {
                    "backupSha256": upload.artifact.sha256,
                    "backupDay": upload.artifact.day.isoformat(),
                },
            },
        )
        if response.is_error:
            if response.status_code == 409 and await self.remote(upload.file_id or ""):
                return upload
            raise BackupError(BackupIssue.GOOGLE, response.status_code)
        session = response.headers.get("Location", "")
        parsed = urlparse(session)
        if parsed.scheme != "https" or parsed.hostname != "www.googleapis.com":
            raise BackupError(BackupIssue.SESSION)
        upload = upload.model_copy(update={"session": session})
        await save(upload)
        return upload

    async def upload(
        self, upload: BackupUpload, save: Callable[[BackupUpload], Awaitable[None]]
    ) -> DriveBackupFile:
        if upload.session:
            parsed = urlparse(upload.session)
            if parsed.scheme != "https" or parsed.hostname != "www.googleapis.com":
                raise BackupError(BackupIssue.SESSION)
        if upload.file_id and (remote := await self.remote(upload.file_id)):
            return self.verify(remote, upload)
        if not upload.session:
            upload = await self.start(upload, save)
        if not upload.session:
            remote = await self.remote(upload.file_id or "")
            if remote:
                return self.verify(remote, upload)
            raise BackupError(BackupIssue.SESSION)
        offset = 0
        status = await self.request(
            "PUT", upload.session, data=b"", headers={"Content-Range": f"bytes */{upload.artifact.size}"}
        )
        if status.status_code in (200, 201):
            return self.verify(DriveBackupFile.model_validate(status.json()), upload)
        if status.status_code in (404, 410):
            upload = await self.start(upload.model_copy(update={"session": None}), save)
        elif status.status_code == 308:
            offset = (
                int(status.headers.get("Range", "bytes=0--1").rsplit("-", 1)[-1]) + 1
                if "Range" in status.headers
                else 0
            )
        else:
            raise BackupError(BackupIssue.SESSION, status.status_code)
        failures = 0
        with upload.artifact.path.open("rb") as stream:
            while offset < upload.artifact.size:
                stream.seek(offset)
                chunk = stream.read(min(DATABASE_BACKUP.chunk_bytes, upload.artifact.size - offset))
                if not chunk:
                    raise BackupError(BackupIssue.INTEGRITY)
                try:
                    response = await self.request(
                        "PUT",
                        upload.session or "",
                        data=chunk,
                        retry=False,
                        headers={
                            "Content-Type": "application/octet-stream",
                            "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{upload.artifact.size}",
                        },
                    )
                except BackupError:
                    if failures >= len(DATABASE_BACKUP.retry_seconds):
                        raise
                    await asyncio.sleep(DATABASE_BACKUP.retry_seconds[failures])
                    failures += 1
                    response = await self.request(
                        "PUT",
                        upload.session or "",
                        data=b"",
                        headers={"Content-Range": f"bytes */{upload.artifact.size}"},
                    )
                if response.status_code in (200, 201):
                    remote = await self.remote(upload.file_id or "")
                    if remote:
                        return self.verify(remote, upload)
                    raise BackupError(BackupIssue.SESSION)
                if response.status_code != 308:
                    raise BackupError(BackupIssue.SESSION, response.status_code)
                confirmed = (
                    int(response.headers.get("Range", "").rsplit("-", 1)[-1]) + 1
                    if "Range" in response.headers
                    else 0
                )
                if not offset <= confirmed <= offset + len(chunk):
                    raise BackupError(BackupIssue.SESSION)
                if confirmed == offset:
                    if failures >= len(DATABASE_BACKUP.retry_seconds):
                        raise BackupError(BackupIssue.SESSION)
                    await asyncio.sleep(DATABASE_BACKUP.retry_seconds[failures])
                    failures += 1
                else:
                    failures = 0
                offset = confirmed
        raise BackupError(BackupIssue.SESSION)
