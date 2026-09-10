"""Revocable, expiring credentials for private employee GPTs (not browser sessions)."""

import hashlib
import hmac
import logging
import os
import secrets
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

logger = logging.getLogger(__name__)


class AgentCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    token_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    user_id: int = Field(gt=0)
    expires_at: AwareDatetime
    name: str = Field(default="Личный GPT", min_length=1, max_length=80)


def save_credentials(path: Path, records: list[AgentCredential]) -> None:
    with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(TypeAdapter(list[AgentCredential]).dump_json(records, indent=2))
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def credential_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    lock = lock_path.open("x")
    try:
        yield
    finally:
        lock.close()
        lock_path.unlink(missing_ok=True)


def read_credentials(path: Path) -> list[AgentCredential]:
    if not path.exists():
        return []
    records = TypeAdapter(list[AgentCredential]).validate_json(path.read_text(encoding="utf-8"))
    if len({record.key_id for record in records}) != len(records):
        raise ValueError("Duplicate agent key IDs")
    return records


def create_credential(user_id: int, expires_at: datetime) -> tuple[str, AgentCredential]:
    key_id = secrets.token_hex(8)
    token = f"csagent_{key_id}_{secrets.token_urlsafe(32)}"
    return token, AgentCredential(
        key_id=key_id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        user_id=user_id,
        expires_at=expires_at,
    )


def resolve_credential(token: str, path: Path) -> AgentCredential | None:
    if len(token) > 128 or not token.startswith("csagent_"):
        return None
    parts = token.split("_", 2)
    if len(parts) != 3:
        return None
    try:
        records = read_credentials(path)
    except (OSError, ValueError, ValidationError):
        logger.error("agent_credentials_unavailable")
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(UTC)
    for record in records:
        if record.key_id == parts[1] and hmac.compare_digest(record.token_hash, digest):
            return record if record.expires_at > now else None
    return None
