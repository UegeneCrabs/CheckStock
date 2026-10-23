from app.core.constants import UiAlertCode
from app.dto.common import DtoModel


class UiUser(DtoModel):
    id: int
    full_name: str
    is_active: bool


class UiStore(DtoModel):
    slug: str
    name: str
    marketplaces: tuple[str, ...]
    configured_marketplaces: tuple[str, ...] = ()


class UiAlert(DtoModel):
    store_slug: str
    marketplace: str
    scope: str
    code: UiAlertCode = UiAlertCode.SYNC_ERROR
    error: str | None = None
    checked_at: str | None = None
    expires_at: str | None = None


class UiContext(DtoModel):
    ok: bool = True
    user: UiUser
    stores: tuple[UiStore, ...]
    sections: tuple[str, ...]
    inbound_interval_seconds: int
    warehouse_display_limit: int
    economics_preview: bool
    alerts: tuple[UiAlert, ...] = ()
