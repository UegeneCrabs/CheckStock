import asyncio
import hashlib
import logging
import os
import signal
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.engine import make_url

from app.config import Settings
from app.core.constants import BackupIssue
from app.dto.database_backup import BackupArtifact, BackupError, BackupReceipt, BackupUpload
from app.infrastructure.process_job import WindowsProcessJob

logger = logging.getLogger(__name__)


class DatabaseBackupStore:
    def __init__(self, config: Settings):
        self.config = config
        identity = (
            make_url(config.database_url).render_as_string(hide_password=True)
            if config.database_url
            else str(config.database_path.resolve())
        )
        self.identity = hashlib.sha256(
            (identity + "|" + config.database_backup.folder_id).encode()
        ).hexdigest()[:16]
        self.directory = config.database_backup.directory.resolve() / self.identity

    def path(self, day: date, suffix: str) -> Path:
        return self.directory / (f"checkstock-{day.isoformat()}-{self.identity}" + suffix)

    def write(self, path: Path, value: BaseModel) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with os.fdopen(
            os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8"
        ) as stream:
            stream.write(value.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    async def receipt(self, day: date) -> BackupReceipt | None:
        path = self.path(day, ".uploaded.json")
        if not path.exists():
            return None
        return BackupReceipt.model_validate_json(path.read_bytes())

    async def prepare(self, day: date) -> BackupUpload:
        logger.info("database_backup_creating day=%s", day)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.path(day, ".dump" if self.config.database_url else ".sqlite.gz")
        environment = os.environ.copy()
        environment.update(
            {
                "CHECKSTOCK_DB_PATH": str(self.config.database_path),
                "CHECKSTOCK_DATABASE_URL": self.config.database_url or "",
                "CHECKSTOCK_BACKUP_PG_DUMP": self.config.database_backup.pg_dump,
                "CHECKSTOCK_BACKUP_PG_RESTORE": self.config.database_backup.pg_restore,
                "CHECKSTOCK_BACKUP_TIMEOUT_SECONDS": str(self.config.database_backup.process_timeout_seconds),
            }
        )
        job = WindowsProcessJob() if sys.platform == "win32" else None
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "scripts.ops.create_database_backup",
            "--output",
            str(path),
            "--day",
            day.isoformat(),
            cwd=self.config.base_dir,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=sys.platform != "win32",
        )
        try:
            if job is not None:
                job.attach(process.pid)
            output, _ = await asyncio.wait_for(
                process.communicate(), self.config.database_backup.process_timeout_seconds * 2 + 60
            )
        except BaseException:
            if process.returncode is None:
                if sys.platform == "win32":
                    assert job is not None
                    job.close()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
            raise
        finally:
            if job is not None:
                job.close()
        if process.returncode:
            raise BackupError(BackupIssue.DUMP)
        artifact = BackupArtifact.model_validate_json(output)
        logger.info("database_backup_created day=%s bytes=%s", day, artifact.size)
        if artifact.path != path:
            raise BackupError(BackupIssue.INTEGRITY)
        pending = self.path(day, ".pending.json")
        if pending.exists():
            upload = BackupUpload.model_validate_json(pending.read_bytes())
            if upload.artifact != artifact or upload.folder_id != self.config.database_backup.folder_id:
                raise BackupError(BackupIssue.INTEGRITY)
        else:
            upload = BackupUpload(folder_id=self.config.database_backup.folder_id, artifact=artifact)
            await self.save_upload(upload)
        return upload

    async def save_upload(self, upload: BackupUpload) -> None:
        self.write(self.path(upload.artifact.day, ".pending.json"), upload)

    async def complete(self, artifact: BackupArtifact, file_id: str) -> None:
        self.write(
            self.path(artifact.day, ".uploaded.json"),
            BackupReceipt(
                folder_id=self.config.database_backup.folder_id,
                artifact=artifact,
                file_id=file_id,
                completed_at=datetime.now(UTC),
            ),
        )

    async def cleanup(self, artifact: BackupArtifact) -> None:
        expected = self.path(artifact.day, ".dump" if self.config.database_url else ".sqlite.gz")
        if artifact.path != expected or artifact.path.is_symlink():
            raise BackupError(BackupIssue.INTEGRITY)
        artifact.path.unlink(missing_ok=True)
        self.path(artifact.day, ".pending.json").unlink(missing_ok=True)
