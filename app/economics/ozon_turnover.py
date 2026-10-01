"""Seven-day Ozon order turnover after cancellations."""

from datetime import date, datetime, timedelta

from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.ozon import tokens as ozon_tokens
from app.repositories import sales as sales_repository
from app.stock import sales as sales_sync

JOB_NAME = "ozon_unit_economics_turnover_sync"
LOOKBACK_DAYS = 21


def sync_all(store_slugs: tuple[str, ...] | None = None) -> dict[str, dict]:
    stores = tuple(STORES) if store_slugs is None else store_slugs
    return {
        slug: sales_sync.sync_store(slug, "OZON", lookback_days=LOOKBACK_DAYS)
        for slug in stores
        if ozon_tokens.has_credentials(slug)
    }


def _available_stores(
    store_slugs: tuple[str, ...], period_from: date, period_to: date, label: str
) -> tuple[tuple[str, ...], dict[str, str]]:
    if not store_slugs:
        return (), {}

    states = {row["store_slug"]: row for row in sales_repository.get_sales_sync_states("OZON")}
    available: list[str] = []
    errors: dict[str, str] = {}
    for slug in store_slugs:
        state = states.get(slug) or {}
        last_success = str(state.get("last_success_at") or "")
        try:
            synced_day = datetime.fromisoformat(last_success.replace("Z", "+00:00")).astimezone(
                MOSCOW_TIMEZONE
            ).date()
        except ValueError:
            synced_day = None
        if not state.get("ok"):
            errors[slug] = label + ": " + str(state.get("error") or "заказы ещё не загружены")
        elif synced_day is None or synced_day < period_to or (
            period_to < datetime.now(MOSCOW_TIMEZONE).date() and synced_day == period_to
        ):
            errors[slug] = label + ": нет свежей полной выгрузки за выбранный период"
        elif period_from < synced_day - timedelta(days=LOOKBACK_DAYS - 1):
            errors[slug] = label + ": выбранный период выходит за пределы выгрузки заказов"
        else:
            available.append(slug)

    return tuple(available), errors


def load(store_slugs: tuple[str, ...], period_from: date, period_to: date) -> tuple[dict[tuple[str, str], float], dict[str, str]]:
    available, errors = _available_stores(store_slugs, period_from, period_to, "ТО Ozon")
    amounts = sales_repository.get_net_order_amounts_by_article(
        "OZON", tuple(available), period_from.isoformat(), period_to.isoformat()
    )
    return amounts, errors


def load_order_counts(
    store_slugs: tuple[str, ...], period_from: date, period_to: date
) -> tuple[dict[tuple[str, str], int], dict[str, str]]:
    available, errors = _available_stores(store_slugs, period_from, period_to, "Заказы Ozon")
    counts = sales_repository.get_order_counts_by_article(
        "OZON", available, period_from.isoformat(), period_to.isoformat()
    )
    return counts, errors
