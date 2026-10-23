from pydantic import Field, SecretStr

from app.core.constants import WEB, CredentialMarketplace, JobScope, ScheduleKind, WebErrorCode
from app.dto.common import DtoModel


class CredentialUpdate(DtoModel):
    api_key: SecretStr = Field(min_length=1, max_length=WEB.api_key_max_length)
    client_id: str = Field(default="", max_length=WEB.client_id_max_length)


class CredentialStatus(DtoModel):
    store_slug: str
    marketplace: CredentialMarketplace
    configured: bool
    client_id_suffix: str | None = None
    expires_at: str | None = None
    error_code: WebErrorCode | None = None


class CredentialList(DtoModel):
    ok: bool = True
    credentials: tuple[CredentialStatus, ...]


class CredentialResult(DtoModel):
    ok: bool = True
    status: CredentialStatus


class WebFailure(DtoModel):
    ok: bool = False
    code: WebErrorCode


class SyncSettingUpdate(DtoModel):
    enabled: bool
    store_slug: str = Field(default="", max_length=WEB.target_max_length)
    marketplace: str = Field(default="", max_length=WEB.target_max_length)


class MarketplaceSetting(DtoModel):
    marketplace: str
    enabled: bool


class JobTarget(MarketplaceSetting):
    store_slug: str
    configured_enabled: bool


class JobConfiguration(DtoModel):
    name: str
    scope: JobScope
    environment_enabled: bool
    configured_enabled: bool
    effective_enabled: bool
    target_count: int
    enabled_target_count: int
    marketplace_settings: tuple[MarketplaceSetting, ...]
    targets: tuple[JobTarget, ...]


class JobConfigurationResult(DtoModel):
    ok: bool = True
    configuration: JobConfiguration


class JobScheduleView(DtoModel):
    kind: ScheduleKind
    timezone: str
    interval_seconds: int | None = None
    hours: tuple[int, ...] = ()
    minute: int = 0
    weekdays: tuple[int, ...] = ()
    deadline_hour: int | None = None
    retry_seconds: int | None = None


class JobView(DtoModel):
    name: str
    title: str
    description: str
    scope: JobScope
    marketplaces: tuple[str, ...]
    manual_run: bool
    schedule: JobScheduleView
    configuration: JobConfiguration


class JobCatalog(DtoModel):
    ok: bool = True
    jobs: tuple[JobView, ...]


class JobState(DtoModel):
    name: str
    status: str | None = None
    running: bool = False
    last_trigger: str | None = None
    last_started_at: str | None = None
    last_finished_at: str | None = None
    last_success_at: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    next_run_at: str | None = None
    updated_at: str | None = None
    cooldown_until: str | None = None
    error_code: WebErrorCode | None = None
    effective_enabled: bool = True


class JobStates(DtoModel):
    ok: bool = True
    states: tuple[JobState, ...]


class JobRun(DtoModel):
    id: str
    name: str
    trigger: str | None = None
    status: str | None = None
    started_at: str
    finished_at: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    error_code: WebErrorCode | None = None


class JobIdentity(DtoModel):
    name: str
    title: str


class JobHistory(DtoModel):
    ok: bool = True
    job: JobIdentity
    retention_days: int = WEB.history_retention_days
    runs: tuple[JobRun, ...]


class JobLaunch(DtoModel):
    ok: bool = True
    run_id: str
