"""Finance is opt-in and uses the existing tracked job mutex for every run."""

from datetime import timedelta

from app.application.finance import today
from app.config import settings
from app.core.stores import STORES

JOB = "yandex_finance_sync"


def run(stores, start, end, actor="scheduler", mode="import", *, scheduled=False):
    from app.container import ApplicationContainer
    from app.repositories.yandex_source_values import get_values
    from app.yandex.finance_provider import FinanceProvider

    service = ApplicationContainer().finance
    if mode != "recalculate":
        for store in stores:
            service.repository.observe_costs(store, get_values(store))
    result = service.synchronize(
        tuple(stores), start, end, FinanceProvider(service.repository), actor, mode=mode, scheduled=scheduled
    )
    service.repository.trim_raw(settings.yandex_finance_raw_retention_days)
    return result


def daily(stores=None):
    from app.jobs import settings as sync_settings

    stores = tuple(STORES) if stores is None else tuple(stores)
    allowed = sync_settings.enabled_stores(JOB, "YANDEX MARKET")
    stores = tuple(s for s in stores if s in allowed)
    if not stores:
        return {"ok": True, "skipped": True}
    end = today() - timedelta(days=1)
    start = today() - timedelta(days=90)
    if today().day == 1 and settings.yandex_finance_reconcile_months:
        month = today().year * 12 + today().month - 1 - settings.yandex_finance_reconcile_months
        start = min(start, today().replace(year=month // 12, month=month % 12 + 1, day=1))
    return run(stores, start, end, scheduled=True)
