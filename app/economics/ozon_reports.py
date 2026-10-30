from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from app.core.constants import OZON_SCENARIO, OzonReportRowKind
from app.dto.ozon_economics import OzonProduct
from app.dto.ozon_margin import OzonMarginDay
from app.dto.ozon_reports import (
    OzonProfitDay,
    OzonProfitMetrics,
    OzonProfitQuery,
    OzonProfitRow,
    OzonReportArticle,
    OzonReportEvidence,
    OzonReportFilters,
)


def number(value: Decimal | float | int | None) -> float | None:
    return float(value) if value is not None else None


def known_sum(values: Iterable[float | None]) -> float | None:
    known = tuple(Decimal(str(value)) for value in values if value is not None)
    return float(sum(known, Decimal(0))) if known else None


def ratio(top: float | None, bottom: float | None, factor: int = 1) -> float | None:
    return top / bottom * factor if top is not None and bottom is not None and bottom > 0 else None


def daily(value: OzonMarginDay, net_orders: int | None) -> OzonProfitDay:
    inputs = value.inputs
    cost = (
        inputs.seller_price - value.margin
        if inputs.seller_price is not None and value.margin is not None
        else None
    )
    return OzonProfitDay(
        date=value.day,
        orders_count=value.orders,
        net_orders_count=net_orders,
        buyout_percent=number(value.buyout_percent),
        expected_buyouts=number(value.expected_buyouts),
        retail_price=number(inputs.seller_price),
        purchase_price=number(inputs.purchase),
        fulfillment_expense=number(inputs.fulfillment),
        commission_value=number(inputs.commission_amount),
        acquiring_value=number(inputs.acquiring_amount),
        team_commission_value=number(inputs.team_amount),
        unallocated_value=number(inputs.unallocated_amount),
        vat_value=number(inputs.vat_amount),
        usn_value=number(inputs.usn_amount),
        logistics=number(inputs.logistics_amount),
        storage=number(inputs.storage_amount),
        advertising_spend=number(value.advertising_spend),
        advertising_per_unit=number(inputs.advertising_amount),
        total_cost=number(cost),
        net_profit=number(value.margin),
        day_profit=number(value.profit),
        day_purchase_value=number(value.purchase_value),
        available=value.profit is not None,
        complete=value.profit is not None and not value.missing and not value.issues,
        missing=value.missing,
        issues=value.issues,
    )


def profit_row(product: OzonProduct, evidence: OzonReportEvidence) -> OzonProfitRow:
    key = product.store_slug, product.article
    orders = {item.day: item for item in evidence.turnover.products.get(key, ())}
    period = product.economics_7d
    days = period.daily
    confirmed = evidence.turnover.eligible_days.get(
        product.store_slug, frozenset()
    ) | evidence.turnover.confirmed_days.get(product.store_slug, frozenset())
    order_rows = tuple(row for day, row in orders.items() if day in confirmed)
    known = any(day.day in confirmed for day in days)
    basis = product.advertising.drr_basis
    buyout = basis.buyout_percent if basis else None
    expected = (
        basis.turnover * Decimal(str(buyout)) / OZON_SCENARIO.percent_factor
        if basis and basis.turnover is not None and buyout is not None
        else None
    )
    quantity = sum(row.ordered + row.cancelled for row in order_rows) if known else None
    coverage = period.margin_coverage
    missing = (
        tuple(sorted({date.fromisoformat(day) for day in (*coverage.missing_dates, *coverage.partial_dates)}))
        if coverage
        else tuple(day.day for day in days)
    )
    return OzonProfitRow(
        store_slug=product.store_slug,
        store_name=product.store_name,
        article=product.article,
        name=product.name,
        image_url=product.image_url,
        manager=product.manager,
        subject=next(
            (item.subject or "" for item in evidence.identities if (item.store_slug, item.article) == key), ""
        ),
        code=product.tag_data.code,
        orders_count=quantity,
        cancel_count=sum(row.cancelled for row in order_rows) if known else None,
        net_orders_count=sum(row.ordered for row in order_rows) if known else None,
        orders_amount=(known_sum(number(row.gross_amount) for row in order_rows) if order_rows else 0)
        if known
        else None,
        cancel_amount=(known_sum(number(row.cancelled_amount) for row in order_rows) if order_rows else 0)
        if known
        else None,
        net_orders_amount=number(period.turnover),
        buyout_percent=buyout,
        buyout_orders_count=quantity,
        expected_buyout_amount=number(expected),
        stock=product.stock.total,
        stock_fbs=product.stock.fbs,
        stock_fbo=product.stock.fbo,
        stock_fulfillment=product.stock.fulfillment,
        stock_days=product.stock.days,
        stock_average_daily_orders=product.stock.average_daily_orders,
        impressions=product.advertising.impressions,
        clicks=product.advertising.clicks,
        ctr=product.advertising.ctr,
        cpc=product.advertising.cpc,
        click_spend=number(product.advertising.click_spend),
        advertising_spend=product.advertising.spend,
        drr=basis.drr if basis else None,
        drr_spend=number(basis.spend) if basis and expected is not None else None,
        margin_orders_count=known_sum(number(day.expected_buyouts) for day in days if day.profit is not None),
        margin=period.margin,
        purchase_value=number(period.purchase_value),
        roi_purchase_value=number(period.roi_purchase_value),
        roi=period.roi,
        margin_complete=period.complete,
        margin_missing_days=missing,
        margin_missing_days_count=len(missing),
        margin_expected_days=len(days),
        margin_available_days=sum(day.profit is not None for day in days),
        unavailable_days=tuple(day.day for day in days if day.profit is None),
        daily_calculations=tuple(
            daily(day, orders[day.day].ordered if day.day in orders else 0 if day.day in confirmed else None)
            for day in days
        ),
    )


def totals(rows: tuple[OzonProfitRow, ...]) -> OzonProfitMetrics:
    additive = (
        "orders_count",
        "cancel_count",
        "net_orders_count",
        "orders_amount",
        "cancel_amount",
        "net_orders_amount",
        "buyout_orders_count",
        "expected_buyout_amount",
        "stock",
        "stock_fbs",
        "stock_fbo",
        "stock_fulfillment",
        "stock_average_daily_orders",
        "impressions",
        "clicks",
        "click_spend",
        "advertising_spend",
        "drr_spend",
        "margin_orders_count",
        "margin",
        "purchase_value",
    )
    sums = {key: known_sum(getattr(row, key) for row in rows) for key in additive}
    included = tuple(row for row in rows if row.margin is not None)
    roi_basis = (
        known_sum(row.roi_purchase_value for row in included)
        if included and all(row.roi_purchase_value is not None for row in included)
        else None
    )
    weighted = known_sum(
        row.buyout_percent * row.buyout_orders_count
        if row.buyout_percent is not None and row.buyout_orders_count is not None
        else None
        for row in rows
    )
    buyout_weight = known_sum(row.buyout_orders_count for row in rows if row.buyout_percent is not None)
    missing = tuple(sorted({day for row in rows for day in row.margin_missing_days}))
    return OzonProfitMetrics.model_validate(
        {
            **sums,
            "buyout_percent": ratio(weighted, buyout_weight),
            "stock_days": ratio(sums["stock"], sums["stock_average_daily_orders"]),
            "ctr": ratio(sums["clicks"], sums["impressions"], int(OZON_SCENARIO.percent_factor)),
            "cpc": ratio(sums["click_spend"], sums["clicks"]),
            "drr": ratio(
                sums["drr_spend"], sums["expected_buyout_amount"], int(OZON_SCENARIO.percent_factor)
            ),
            "roi_purchase_value": roi_basis,
            "roi": ratio(sums["margin"], roi_basis, int(OZON_SCENARIO.percent_factor)),
            "margin_complete": all(row.margin_complete for row in rows),
            "margin_missing_days": missing,
            "margin_missing_days_count": len(missing),
            "margin_expected_days": max((row.margin_expected_days for row in rows), default=0),
            "margin_available_days": min((row.margin_available_days for row in rows), default=0),
            "unavailable_days": tuple(sorted({day for row in rows for day in row.unavailable_days})),
        }
    )


def categories(rows: tuple[OzonProfitRow, ...]) -> tuple[OzonProfitRow, ...]:
    groups: dict[str, list[OzonProfitRow]] = defaultdict(list)
    for row in rows:
        groups[row.subject].append(row)
    return tuple(
        OzonProfitRow(
            **totals(tuple(items)).model_dump(),
            name=subject,
            subject=subject,
            row_kind=OzonReportRowKind.CATEGORY,
            product_count=len(items),
        )
        for subject, items in sorted(groups.items())
    )


def selected(row: OzonProfitRow, query: OzonProfitQuery) -> bool:
    return all(
        not values or (value or "").casefold() in {item.casefold() for item in values}
        for value, values in (
            (row.subject, query.subject),
            (row.manager, query.manager),
            (row.article, query.article),
        )
    )


def filters(rows: tuple[OzonProfitRow, ...]) -> OzonReportFilters:
    return OzonReportFilters(
        subjects=tuple(sorted({row.subject for row in rows})),
        managers=tuple(sorted({row.manager for row in rows if row.manager})),
        articles=tuple(
            OzonReportArticle(
                article=row.article,
                name=row.name,
                store_slug=row.store_slug,
                store_name=row.store_name,
                image_url=row.image_url,
            )
            for row in rows
        ),
    )
