from dataclasses import dataclass
from typing import Protocol, TypedDict

from app.core.constants import (
    JobScope,
)


class AsyncJobResult(Protocol):
    @property
    def error(self) -> str | None: ...


class SyncJobSetting(TypedDict):
    store_slug: str
    marketplace: str
    enabled: bool


class SyncMarketplaceSetting(TypedDict):
    marketplace: str
    enabled: bool


class SyncJobTarget(SyncMarketplaceSetting):
    store_slug: str
    configured_enabled: bool


class SyncJobConfiguration(TypedDict):
    name: str
    scope: JobScope
    environment_enabled: bool
    configured_enabled: bool
    effective_enabled: bool
    target_count: int
    enabled_target_count: int
    marketplace_settings: list[SyncMarketplaceSetting]
    targets: list[SyncJobTarget]


@dataclass(frozen=True, slots=True)
class JobSchedule:
    name: str
    next_run_at: str
    attempt_number: int
    active_run_id: str | None
    updated_at: str
