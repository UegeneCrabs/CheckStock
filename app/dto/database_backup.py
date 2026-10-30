from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.constants import BackupIssue
from app.dto.common import DtoModel


class BackupError(Exception):
    def __init__(self, code: BackupIssue, status: int | None = None):
        self.code, self.status = code, status
        super().__init__(code.value + (f": HTTP {status}" if status else ""))


class BackupArtifact(DtoModel):
    day: date
    path: Path
    size: int = Field(gt=0)
    md5: str = Field(pattern=r"^[0-9a-f]{32}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BackupUpload(DtoModel):
    folder_id: str
    artifact: BackupArtifact
    file_id: str | None = None
    session: str | None = None


class BackupReceipt(DtoModel):
    folder_id: str
    artifact: BackupArtifact
    file_id: str
    completed_at: datetime


class DriveBackupFile(BaseModel):
    id: str
    name: str = ""
    size: int = 0
    md5: str | None = Field(default=None, alias="md5Checksum")
    url: str | None = Field(default=None, alias="webViewLink")


class BackupConfiguration(DtoModel):
    folder_id: str
    configured: bool
    database: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BackupJobResult:
    file_id: str | None = None
    skipped: bool = False
    error: str | None = None
