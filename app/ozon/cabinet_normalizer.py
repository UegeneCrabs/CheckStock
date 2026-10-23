from datetime import datetime, timedelta

from app.core.constants import OZON_CABINET, OZON_LOGISTICS, TIME, OzonBuyoutState
from app.dto.ozon_buyout import OzonBuyoutEvidence
from app.dto.ozon_cabinet import (
    OzonBuyoutProduct,
    OzonBuyoutSnapshot,
    OzonCabinetValues,
    OzonEffectiveBuyout,
    OzonEffectiveParameters,
    OzonProductGoals,
)


def returned_quantities(evidence: OzonBuyoutEvidence, now: datetime) -> dict[str, int]:
    lines = {(line.order_key, line.line_key): line for line in evidence.lines}
    if len(lines) != len(evidence.lines):
        raise ValueError("Duplicate buyout order lines")
    replaced = {item.source_id for item in evidence.returns if item.source_id}
    quantities: dict[tuple[str, str], int] = {}
    returned: dict[str, int] = {}
    for item in evidence.returns:
        if item.id in replaced or item.type != OZON_LOGISTICS.client_return_type:
            continue
        if item.logistic.return_date is None or item.logistic.return_date > now:
            continue
        key = item.posting_number, item.product.sku
        line = lines.get(key)
        if line is None or line.article != item.product.offer_id:
            raise ValueError("Unmatched buyout return")
        quantity = quantities.get(key, 0) + item.product.quantity
        quantities[key] = quantity
        if quantity > line.sold_quantity:
            if line.sold_quantity == 0 and quantity <= line.cancelled_quantity:
                continue
            raise ValueError("Buyout returns exceed delivered quantity")
        returned[line.article] = returned.get(line.article, 0) + item.product.quantity
    return returned


def buyout_snapshot(
    store: str, period_days: int, articles: tuple[str, ...], evidence: OzonBuyoutEvidence, now: datetime
) -> OzonBuyoutSnapshot:
    data = evidence.turnover
    returns = returned_quantities(evidence, now)
    today = now.astimezone(TIME.moscow_timezone).date()
    start = today - timedelta(days=period_days)
    end = today - timedelta(days=1)
    dates = frozenset(start + timedelta(days=offset) for offset in range(period_days))
    missing = dates - data.eligible_days.get(store, frozenset())
    products = []
    for article in sorted(set(articles) | {article for slug, article in data.products if slug == store}):
        days = tuple(day for day in data.products.get((store, article), ()) if day.day in dates)
        received = sum(day.received for day in days)
        cancelled = sum(day.cancelled for day in days)
        returned = returns.get(article, 0)
        if returned > received:
            raise ValueError("Buyout returns exceed cohort deliveries")
        products.append(
            OzonBuyoutProduct(
                article=article,
                received=received,
                cancelled=cancelled,
                returned=returned,
                percent=round(
                    (received - returned) / (received + cancelled) * OZON_CABINET.percent_factor,
                    OZON_CABINET.percent_precision,
                )
                if received + cancelled and not missing
                else None,
            )
        )
    return OzonBuyoutSnapshot(
        formula_version=OZON_CABINET.buyout_formula_version,
        store_slug=store,
        day=today,
        period_from=start,
        period_to=end,
        observed_at=now,
        products=tuple(products),
        state=OzonBuyoutState.PARTIAL if missing else OzonBuyoutState.AVAILABLE,
        missing_dates=tuple(sorted(missing)),
    )


def effective_parameters(
    settings: OzonCabinetValues,
    article: str,
    code: str | None,
    product_goals: OzonProductGoals,
    snapshot: OzonBuyoutSnapshot | None,
    now: datetime,
) -> OzonEffectiveParameters:
    today = now.astimezone(TIME.moscow_timezone).date()
    start = today - timedelta(days=settings.buyout.period_days)
    end = today - timedelta(days=1)
    item = next((item for item in snapshot.products if item.article == article), None) if snapshot else None
    matches = bool(
        snapshot
        and snapshot.formula_version == OZON_CABINET.buyout_formula_version
        and snapshot.period_from == start
        and snapshot.period_to == end
    )
    actual = item.percent if item and matches else None
    use_default = (actual is None or actual == 0) and settings.buyout.default_percent is not None
    buyout = OzonEffectiveBuyout(
        percent=settings.buyout.default_percent if use_default else actual,
        actual_percent=actual,
        default_applied=use_default,
        state=snapshot.state if snapshot and matches else OzonBuyoutState.MISSING,
        stale=bool(
            snapshot
            and (
                snapshot.error
                or not matches
                or (now - snapshot.observed_at).total_seconds() > OZON_CABINET.interval_seconds
            )
        ),
        error=snapshot.error if snapshot else None,
        period_from=start,
        period_to=end,
        observed_at=snapshot.observed_at if snapshot else None,
        received=item.received if item and matches else None,
        cancelled=item.cancelled if item and matches else None,
        returned=item.returned if item and matches else None,
        retained=item.received - item.returned if item and matches else None,
    )
    roi = next((value for key, value in settings.goals.target_roi_by_code.items() if key == code), None)
    return OzonEffectiveParameters(
        target_drr_percent=product_goals.target_drr_percent
        if product_goals.target_drr_percent is not None
        else settings.goals.target_drr_percent,
        target_roi_percent=product_goals.target_roi_percent
        if product_goals.target_roi_percent is not None
        else roi
        if roi is not None
        else settings.goals.target_roi_percent,
        expenses=settings.expenses,
        buyout=buyout,
    )
