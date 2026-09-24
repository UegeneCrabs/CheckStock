"""Financial-report pages. Data import is connected separately from the presentation layer."""

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse

from app.access.access_control import accessible_stores
from app.core.stores import STORES
from app.repositories import sales, yandex_financial
from app.web.templating import fill_template, render_page

router = APIRouter()

_DEFAULT_FINANCE_VALUES = {
    "sales": None,
    "orders_turnover": None,
    "buyout_seller_turnover": None,
    "buyout_buyer_turnover": None,
    "buyout_count": None,
    "payout": None,
    "cost_of_goods": None,
    "sold_cost_of_goods": None,
    "returned_cost_of_goods": None,
    "direct_expenses": None,
    "commission_with_spp": None,
    "commission": None,
    "spp": None,
    "storage": None,
    "logistics": None,
    "paid_acceptance": None,
    "other_direct_expenses": None,
    "gross_profit": None,
    "marketing": None,
    "advertising": None,
    "external_advertising": None,
    "cabinet_advertising": None,
    "other": None,
    "unrecognized": None,
    "operating_profit": None,
    "taxes": None,
    "vat": None,
    "bank_receipt": None,
    "marginal_profit": None,
    "marginal_profit_percent": None,
}


def _buyout_count_from_sources(financial_count: int | None, fallback_count: int | None) -> int | None:
    """Prefer an exact marketplace financial-export count over an estimate."""

    return financial_count if financial_count is not None else fallback_count


def _calculate_derived_values(values: dict) -> None:
    """Add totals that are derived from the canonical financial report values."""

    if all(values.get(key) is not None for key in ("sales", "cost_of_goods", "direct_expenses")):
        values["gross_profit"] = round(
            float(values["sales"]) - float(values["cost_of_goods"]) - float(values["direct_expenses"]), 2
        )
    if all(values.get(key) is not None for key in ("gross_profit", "marketing", "other")):
        values["operating_profit"] = round(
            float(values["gross_profit"]) - float(values["marketing"]) - float(values["other"]), 2
        )
    if not all(values.get(key) is not None for key in ("bank_receipt", "sold_cost_of_goods")):
        return

    marginal_profit = round(float(values["bank_receipt"]) - float(values["sold_cost_of_goods"]), 2)
    values["marginal_profit"] = marginal_profit
    buyer_turnover = values.get("buyout_buyer_turnover")
    if buyer_turnover not in (None, 0):
        values["marginal_profit_percent"] = round(marginal_profit / float(buyer_turnover) * 100, 2)


@router.get("/finance-reports/yandex", response_class=HTMLResponse)
async def yandex_finance_report(request: Request):
    content = fill_template("finance/yandex.html")
    return render_page(
        "CheckStock — Финансовые отчеты — Яндекс",
        "finance_yandex",
        content,
        request.state.user,
        content_class="content--finance-report",
    )


@router.get("/api/finance-reports/yandex")
async def yandex_finance_report_data(request: Request, date_from: date, date_to: date, store_slug: str | None = None):
    if date_to < date_from or (date_to - date_from).days > 366:
        raise HTTPException(422, "Выберите период от 1 до 367 дней")
    stores = accessible_stores(request.state.user, "YANDEX MARKET")
    if store_slug:
        if store_slug not in stores:
            raise HTTPException(404, "Магазин недоступен")
        stores = [store_slug]

    def load() -> list[dict]:
        return [
            row
            for store in stores
            for row in sales.get_sales_daily(
                date_from.isoformat(), (date_to + timedelta(days=1)).isoformat(), "YANDEX MARKET", store
            )
        ]

    rows = await run_in_threadpool(load)
    pnl = await run_in_threadpool(
        yandex_financial.pnl_summary, date_from.isoformat(), date_to.isoformat(), store_slug
    )
    # Turnover of orders is recorded on the order creation date before cancellations.
    # This is the "Заказано" metric of the marketplace funnel, not net sales.
    orders_turnover = round(
        sum(
            float(row.get("orders_amount") or 0) + float(row.get("cancellations_amount") or 0) for row in rows
        ),
        2,
    )
    buyout_turnover = round(sum(float(row.get("sales_amount") or 0) for row in rows), 2)
    # A single line-item basket from the official unified financial archive
    # defines all redeemed-goods metrics: quantity, turnover in both prices
    # and cost.  Business Orders remains the source only for the order funnel.
    has_data = bool(rows or pnl)
    values = _DEFAULT_FINANCE_VALUES | {
        "sales": buyout_turnover if rows else None,
        "orders_turnover": orders_turnover if rows else None,
        "buyout_seller_turnover": buyout_turnover if rows else None,
    }
    if pnl:
        values.update(pnl)
    # Financial report rows represent order lines and omit an item's count.
    # Keep funnel turnover from Business Orders API, which contains both.
    values["orders_turnover"] = orders_turnover if rows else None
    values["buyout_count"] = pnl.get("buyout_count") if pnl else None
    _calculate_derived_values(values)
    return {
        "ok": True,
        "has_data": has_data,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "values": values,
    }


@router.get("/api/finance-reports/yandex/stores")
async def yandex_finance_report_stores(request: Request):
    return {
        "ok": True,
        "stores": [
            {"slug": store_slug, "name": STORES[store_slug].name}
            for store_slug in accessible_stores(request.state.user, "YANDEX MARKET")
            if store_slug in STORES
        ],
    }
