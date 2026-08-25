import json
import logging
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from app import db, unit_economics_1c, unit_economics_1c_prices
from app import unit_economics_1c_source_data as unit_economics_1c_source
from app.domain import MOSCOW_TIMEZONE
from app.dto.identity import SectionAccessLevel, SectionName
from app.dto.unit_economics_1c import (
    UnitEconomics1CCabinetSettingsWebRequest,
    UnitEconomics1CPriceChangeRequest,
    UnitEconomics1CProductSettings,
    UnitEconomics1CProductSettingsRequest,
)
from app.section_access import has_access as has_section_access
from app.stores import STORES
from app.web.access import accessible_store_slugs, has_store_access
from app.web.templating import fill_template, render_page

router = APIRouter()
logger = logging.getLogger(__name__)


def _cabinet_settings_payload(store_slugs: tuple[str, ...]) -> list[dict]:
    settings = db.list_unit_economics_1c_cabinet_settings(store_slugs)
    return [
        {
            **item.model_dump(mode="json"),
            "store_name": STORES[item.store_slug]["name"],
            "store_initials": STORES[item.store_slug]["initials"],
            "store_color": STORES[item.store_slug]["color"],
            "store_text": STORES[item.store_slug]["text"],
        }
        for item in settings
    ]


def _price_value(value: object) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _nm_id(article: object) -> str:
    return str(article or "").partition(" / ")[0].strip()


def _optional_integer(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _unit_economics_1c_mock_product(
    store_slug: str,
    product: dict,
    price_snapshot: dict | None = None,
    acquiring_percent: float = 3.8,
    product_metrics: dict | None = None,
    product_settings: UnitEconomics1CProductSettings | None = None,
    acceptance_coefficient: float = 0,
    team_commission_percent: float = 0,
    vat_percent: float = 0,
    usn_percent: float = 0,
    osno_percent: float = 0,
    tax_system: str = "usn",
    product_reference: dict | None = None,
    wb_extra_tariff_percent: float = 0,
    stock_history_by_day: dict[str, dict] | None = None,
    stock_order_metrics: dict | None = None,
) -> dict:
    """Build the 1C layout while keeping WB prices, orders and advertising real."""
    article = str(product.get("article") or "").strip()
    price_snapshot = price_snapshot or {}
    product_metrics = product_metrics or unit_economics_1c.empty_product_metrics()
    product_settings = product_settings or UnitEconomics1CProductSettings(
        store_slug=store_slug,
        article=article,
    )
    product_reference = product_reference or {}
    stock_history_by_day = stock_history_by_day or {}
    stock_order_metrics = stock_order_metrics or {}
    spp_price = _price_value(price_snapshot.get("customer_price_with_spp"))
    wallet_price = _price_value(price_snapshot.get("customer_price_with_wallet"))
    current_price = _price_value(price_snapshot.get("retail_price"))
    orders_count = max(int(product_metrics.get("orders_count") or 0), 0)
    average_customer_price = (
        round(float(product_metrics.get("orders_amount") or 0) / orders_count, 2) if orders_count else None
    )
    calculation_price = (
        spp_price
        if spp_price is not None
        else current_price
        if current_price is not None
        else average_customer_price or 0.0
    )
    economics_retail_price = (
        current_price
        if current_price is not None
        else _price_value(product_metrics.get("average_retail_price"))
    )
    if economics_retail_price is None:
        economics_retail_price = calculation_price
    fbs_stock = max(_optional_integer(product.get("fbs_stock")) or 0, 0)
    fbo_stock = max(_optional_integer(product.get("fbo_stock")) or 0, 0)
    fulfillment_stock = max(_optional_integer(product.get("ff_available")) or 0, 0)
    total_stock = fbs_stock + fbo_stock + fulfillment_stock
    stock_period_days = max(
        int(stock_order_metrics.get("period_days") or unit_economics_1c.STOCK_COVERAGE_PERIOD_DAYS),
        1,
    )
    stock_orders_count = max(int(stock_order_metrics.get("orders_count") or 0), 0)
    average_daily_orders = round(stock_orders_count / stock_period_days, 2)
    stock_days = unit_economics_1c.calculate_stock_coverage_days(
        total_stock,
        stock_orders_count,
        period_days=stock_period_days,
    )
    store = STORES[store_slug]
    paid_acceptance_cost = unit_economics_1c.calculate_paid_acceptance_cost(
        product_settings.volume_l,
        acceptance_coefficient,
    )
    logistics = unit_economics_1c.calculate_delivery_with_returns(
        product_settings.delivery_wb_rub,
        product_settings.buyout_percent,
        product_settings.return_cost_rub,
        paid_acceptance_cost,
    )
    abc_code = str(product_reference.get("abc_code") or "").strip() or None
    turnover_days = _optional_integer(product_reference.get("turnover_days"))
    if turnover_days is None:
        turnover_days = 21
    purchase_price = _price_value(product_reference.get("purchase_price"))
    if purchase_price is None:
        purchase_price = 0.0
    fulfillment_cost = _price_value(product_reference.get("fulfillment_cost"))
    if fulfillment_cost is None:
        fulfillment_cost = 0.0
    source_team_commission = _price_value(product_reference.get("team_commission_percent"))
    effective_team_commission = (
        source_team_commission if source_team_commission is not None else round(team_commission_percent, 2)
    )
    category = str(product_reference.get("category") or "").strip() or None
    subject_commission_percent = _price_value(product_reference.get("subject_commission_percent"))
    has_subject_commission = subject_commission_percent is not None
    if subject_commission_percent is None:
        subject_commission_percent = 0.0
    extra_tariff_percent = round(max(float(wb_extra_tariff_percent or 0), 0.0), 2)
    commission_percent = round(subject_commission_percent + extra_tariff_percent, 2)
    commission_value = round(economics_retail_price * commission_percent / 100, 2)
    storage_sum = (
        round(turnover_days * product_settings.storage_wb_rub, 2) if turnover_days is not None else None
    )
    vat_rate = max(float(vat_percent or 0), 0.0)
    usn_rate = max(float(usn_percent or 0), 0.0)
    osno_rate = max(float(osno_percent or 0), 0.0)
    effective_tax_system = "osno" if store_slug == "gogol" and str(tax_system).lower() == "osno" else "usn"
    tax_components = (
        unit_economics_1c.calculate_tax_components(
            calculation_price,
            vat_rate,
            usn_rate,
            osno_rate,
            effective_tax_system,
        )
        if calculation_price is not None
        else None
    )
    measured_buyout_percent = min(
        max(float(product_metrics.get("buyout_percent") or 0), 0.0),
        100.0,
    )
    buyout_ratio = measured_buyout_percent / 100
    history = []
    period_margin_without_buyout = 0.0
    period_margin_complete = True
    period_purchase_without_buyout = 0.0 if purchase_price is not None else None
    metric_history = {
        str(item.get("date")): item for item in product_metrics.get("daily") or [] if isinstance(item, dict)
    }
    try:
        history_end = date.fromisoformat(str(product_metrics.get("period_to")))
    except ValueError:
        history_end = date.today()
    for offset in range(6, -1, -1):
        day = history_end - timedelta(days=offset)
        day_metrics = metric_history.get(day.isoformat()) or {}
        day_ads = round(float(day_metrics.get("advertising_spend") or 0), 2)
        day_orders_amount = round(float(day_metrics.get("orders_amount") or 0), 2)
        day_orders_count = max(int(day_metrics.get("orders_count") or 0), 0)
        day_drr = _price_value(day_metrics.get("drr"))
        if day_drr is None:
            day_drr = unit_economics_1c.calculate_drr_percent(day_ads, day_orders_amount)
        purchased_units = round(day_orders_count * buyout_ratio, 2)
        day_unit_profit = unit_economics_1c.calculate_unit_profit(
            retail_price=economics_retail_price,
            customer_price=calculation_price,
            acquiring_percent=acquiring_percent,
            delivery_with_returns=logistics,
            storage_wb_rub=product_settings.storage_wb_rub,
            turnover_days=turnover_days,
            wb_commission_percent=commission_percent,
            drr_percent=day_drr,
            purchase_price=purchase_price,
            fulfillment_cost=fulfillment_cost,
            team_commission_percent=effective_team_commission,
            vat_percent=vat_rate,
            usn_percent=usn_rate,
            osno_percent=osno_rate,
            tax_system=effective_tax_system,
        )
        day_margin = (
            round(day_unit_profit["margin"] * purchased_units, 2) if day_unit_profit is not None else None
        )
        if day_unit_profit is None and day_orders_count > 0:
            period_margin_complete = False
        elif day_unit_profit is not None:
            period_margin_without_buyout += day_unit_profit["margin"] * day_orders_count
        if period_purchase_without_buyout is not None:
            period_purchase_without_buyout += purchase_price * day_orders_count
        day_purchase_value = (
            round(purchase_price * purchased_units, 2) if purchase_price is not None else None
        )
        day_stock = stock_history_by_day.get(day.isoformat()) or {}
        history_fbs = _optional_integer(day_stock.get("fbs"))
        history_fbo = _optional_integer(day_stock.get("fbo"))
        history_fulfillment = _optional_integer(day_stock.get("fulfillment"))
        history.append(
            {
                "date": day.isoformat(),
                "label": day.strftime("%d.%m"),
                "margin_rub": day_margin,
                "advertising_rub": day_ads,
                "drr_percent": day_drr,
                "orders_count": day_orders_count,
                "purchased_units": purchased_units,
                "buyout_percent": measured_buyout_percent,
                "turnover_rub": day_orders_amount,
                "fbs_units": history_fbs if history_fbs is not None else fbs_stock if offset == 0 else None,
                "fbo_units": history_fbo if history_fbo is not None else fbo_stock if offset == 0 else None,
                "fulfillment_units": (
                    history_fulfillment
                    if history_fulfillment is not None
                    else fulfillment_stock
                    if offset == 0
                    else None
                ),
                "purchase_value": day_purchase_value,
            }
        )

    period_turnover = round(float(product_metrics.get("orders_amount") or 0), 2)
    period_margin = round(period_margin_without_buyout, 2) if period_margin_complete else None
    period_purchase_value = (
        round(period_purchase_without_buyout, 2) if period_purchase_without_buyout is not None else None
    )
    period_roi = (
        round(period_margin / period_purchase_value * 100, 2)
        if period_margin is not None and period_purchase_value and period_purchase_value > 0
        else 0.0
    )

    return {
        "id": f"{store_slug}:{article}",
        "store_slug": store_slug,
        "store_name": store["name"],
        "store_initials": store["initials"],
        "store_color": store["color"],
        "store_text": store["text"],
        "article": article,
        "barcode": str(product.get("barcode") or "").strip(),
        "name": str(product.get("name") or article).strip(),
        "image_url": str(product.get("image_url") or "").strip(),
        "rating": None,
        "tag": str(product_reference.get("tag_raw") or "").strip() or None,
        "tag_data": {
            "goal_week": _price_value(product_reference.get("goal_week")),
            "goal_day": _price_value(product_reference.get("goal_day")),
            "status": str(product_reference.get("stock_status") or "").strip() or None,
            "ends": str(product_reference.get("stock_end_week") or "").strip() or None,
            "code": abc_code,
            "fact": _price_value(product_reference.get("fact_sales")),
            "plan": _price_value(product_reference.get("plan_sales")),
        },
        "advertising": {
            "spend": round(float(product_metrics.get("spend") or 0), 2),
            "drr": round(float(product_metrics.get("drr") or 0), 2),
            "orders": int(product_metrics.get("orders_count") or 0),
            "sold": int(product_metrics.get("sold_count") or 0),
            "buyout_percent": measured_buyout_percent,
            "orders_amount": round(float(product_metrics.get("orders_amount") or 0), 2),
            "period_from": product_metrics.get("period_from"),
            "period_to": product_metrics.get("period_to"),
            "period_days": int(product_metrics.get("period_days") or 7),
        },
        "economics_7d": {
            "turnover": period_turnover,
            "margin": period_margin,
            "roi": period_roi,
            "purchase_value": period_purchase_value,
            "orders": int(product_metrics.get("orders_count") or 0),
            "period_from": product_metrics.get("period_from"),
            "period_to": product_metrics.get("period_to"),
        },
        "stock": {
            "fbs": fbs_stock,
            "fbo": fbo_stock,
            "fulfillment": fulfillment_stock,
            "total": total_stock,
            "days": stock_days,
            "orders_21d": stock_orders_count,
            "average_daily_orders": average_daily_orders,
            "period_days": stock_period_days,
            "period_from": stock_order_metrics.get("period_from"),
            "period_to": stock_order_metrics.get("period_to"),
        },
        "price": {
            "base": _price_value(price_snapshot.get("seller_base_price")),
            "current": current_price,
            "club": _price_value(price_snapshot.get("club_discounted_price")),
            "with_spp": spp_price,
            "with_wallet": wallet_price,
            "snapshot_date": price_snapshot.get("day"),
            "window_days": price_snapshot.get("customer_price_window_days"),
            "orders_count": price_snapshot.get("customer_price_orders_count"),
            "retail_synced_at": price_snapshot.get("retail_synced_at"),
            "orders_synced_at": price_snapshot.get("orders_synced_at"),
        },
        "details": {
            "subject": category,
            "commission_scheme": ("СУ + доп. тариф WB" if has_subject_commission else None),
            "drr": None,
            "planned_advertising": None,
            "purchase_cost": purchase_price,
            "fulfillment_cost": fulfillment_cost,
            "vat_percent": vat_rate,
            "usn_percent": usn_rate,
            "osno_percent": osno_rate,
            "tax_system": effective_tax_system,
            "acquiring": round(acquiring_percent, 2),
            "team_commission_percent": effective_team_commission,
            "buyout_percent": product_settings.buyout_percent,
            "logistics_type": None,
            "actual_advertising": round(float(product_metrics.get("spend") or 0), 2),
            "delivery_wb_rub": product_settings.delivery_wb_rub,
            "return_cost_rub": product_settings.return_cost_rub,
            "volume_l": product_settings.volume_l,
            "paid_acceptance_cost": paid_acceptance_cost,
            "delivery_with_returns": logistics,
            "storage_wb_rub": product_settings.storage_wb_rub,
            "storage_days": turnover_days,
            "storage_sum": storage_sum,
            "irp_percent": None,
            "spp_price": spp_price,
            "subject_commission_percent": subject_commission_percent,
            "wb_extra_tariff_percent": extra_tariff_percent,
            "commission_percent": commission_percent,
            "commission_value": commission_value,
            "logistics": logistics,
            "vat_value": round(tax_components["vat"], 2) if tax_components else None,
            "usn_value": round(tax_components["usn"], 2) if tax_components else None,
            "osno_value": round(tax_components["osno"], 2) if tax_components else None,
            "tax_value": round(tax_components["total"], 2) if tax_components else None,
            "margin_rub": None,
            "roi": None,
        },
        "product_settings": product_settings.model_dump(mode="json"),
        "history": history,
    }


@router.get("/sales/unit-economics-1c", response_class=HTMLResponse)
async def sales_unit_economics_1c(request: Request):
    store_slugs = accessible_store_slugs(request.state.user)

    def load_products() -> tuple[list[dict], list[dict]]:
        latest_rows = db.get_unit_economics_1c_latest_daily_prices(store_slugs)
        prices = {(str(row["store_slug"]), str(row["article"])): row for row in latest_rows}
        metrics = unit_economics_1c.load_product_metrics(store_slugs)
        stock_order_metrics = unit_economics_1c.load_product_average_daily_orders(
            store_slugs,
            period_days=unit_economics_1c.STOCK_COVERAGE_PERIOD_DAYS,
        )
        settings = {item.store_slug: item for item in db.list_unit_economics_1c_cabinet_settings(store_slugs)}
        product_settings = {
            (item.store_slug, item.article): item
            for item in db.list_unit_economics_1c_product_settings(store_slugs)
        }
        product_references = {
            (str(item["store_slug"]), str(item["article"])): item
            for item in db.get_unit_economics_1c_product_reference_rows(store_slugs)
        }
        history_to = datetime.now(MOSCOW_TIMEZONE).date()
        stock_history_rows = db.get_daily_stock_history(
            store_slugs,
            "WB",
            (history_to - timedelta(days=7)).isoformat(),
            history_to.isoformat(),
        )
        stock_history = {}
        for item in stock_history_rows:
            key = (str(item["store_slug"]), str(item["article"]))
            stock_history.setdefault(key, {})[str(item["day"])] = item
        products: list[dict] = []
        for store_slug in store_slugs:
            cabinet = settings[store_slug]
            catalog = db.get_stock_items(store_slug, "WB")
            for product in catalog:
                article = str(product.get("article") or "")
                products.append(
                    _unit_economics_1c_mock_product(
                        store_slug=store_slug,
                        product=product,
                        price_snapshot=prices.get((store_slug, article)),
                        acquiring_percent=cabinet.acquiring_percent,
                        product_metrics=metrics.get((store_slug, _nm_id(article))),
                        product_settings=product_settings.get((store_slug, article)),
                        acceptance_coefficient=cabinet.acceptance_coefficient,
                        team_commission_percent=cabinet.team_commission_percent,
                        vat_percent=cabinet.vat_percent,
                        usn_percent=cabinet.usn_percent,
                        osno_percent=cabinet.osno_percent,
                        tax_system=cabinet.tax_system,
                        product_reference=product_references.get((store_slug, article)),
                        wb_extra_tariff_percent=cabinet.wb_extra_tariff_percent,
                        stock_history_by_day=stock_history.get((store_slug, article)),
                        stock_order_metrics=stock_order_metrics.get((store_slug, _nm_id(article))),
                    )
                )

        states = {
            str(row["store_slug"]): row for row in db.list_unit_economics_1c_price_sync_states(store_slugs)
        }
        warnings: list[dict] = []
        for store_slug in store_slugs:
            state = states.get(store_slug)
            if state is not None and state.get("status") == "ok":
                continue
            message = (
                str(state.get("error") or "цены обновились не полностью")
                if state is not None
                else "цены ещё не синхронизировались"
            )
            warnings.append(
                {
                    "store_slug": store_slug,
                    "store_name": STORES[store_slug]["name"],
                    "status": str(state.get("status") or "error") if state else "error",
                    "message": message,
                }
            )
        return products, warnings

    products, price_warnings = await run_in_threadpool(load_products)
    price_alerts = [
        {
            "title": (
                f"Кабинет {warning['store_name']}: использована резервная цена"
                if warning["status"] == "fallback"
                else f"Кабинет {warning['store_name']}: цены не обновились"
            ),
            "text": warning["message"],
        }
        for warning in price_warnings
    ]
    unit_config = {
        "stores": [
            {
                "slug": slug,
                "name": STORES[slug]["name"],
                "initials": STORES[slug]["initials"],
                "color": STORES[slug]["color"],
                "text": STORES[slug]["text"],
            }
            for slug in store_slugs
        ],
        "products": products,
        "canEdit": has_section_access(
            request.state.user,
            SectionName.UNIT_ECONOMICS_1C,
            SectionAccessLevel.WRITE,
        ),
    }
    content = fill_template(
        "unit_economics_1c_content.html",
        unit_1c_config=json.dumps(unit_config, ensure_ascii=False).replace("</", "<\\/"),
    )
    return render_page(
        "CheckStock — Юнит-экономика 1С — Wildberries",
        "unit_1c_wb",
        content,
        request.state.user,
        content_class="content--unit-1c",
        alerts=price_alerts,
    )


@router.post("/api/unit-economics-1c/sync")
async def unit_economics_1c_sync(request: Request):
    store_slugs = accessible_store_slugs(request.state.user)
    report = await run_in_threadpool(unit_economics_1c.sync_stores, store_slugs)
    return {"ok": all(item.get("ok") for item in report.values()), "report": report}


@router.post("/api/unit-economics-1c/prices/sync")
async def unit_economics_1c_prices_sync(request: Request):
    store_slugs = accessible_store_slugs(request.state.user)
    report = await run_in_threadpool(unit_economics_1c_prices.sync_stores, store_slugs)
    return {"ok": all(item.get("ok") for item in report.values()), "report": report}


@router.post("/api/unit-economics-1c/prices")
async def unit_economics_1c_prices_submit(
    request: Request,
    payload: UnitEconomics1CPriceChangeRequest,
):
    if not has_section_access(
        request.state.user,
        SectionName.UNIT_ECONOMICS_1C,
        SectionAccessLevel.WRITE,
    ):
        return JSONResponse({"ok": False, "error": "Нет права изменять цены"}, status_code=403)

    changes_by_store: dict[str, list[dict]] = {}
    for item in payload.data:
        store_slug = item.store_slug.lower()
        if store_slug not in STORES:
            return JSONResponse({"ok": False, "error": f"Кабинет {store_slug} не найден"}, status_code=404)
        if not has_store_access(request.state.user, store_slug):
            return JSONResponse(
                {"ok": False, "error": f"Нет доступа к кабинету {STORES[store_slug]['name']}"},
                status_code=403,
            )
        changes_by_store.setdefault(store_slug, []).append(
            {
                "article": item.article,
                "target_price": item.target_price,
                "target_kind": item.target_kind,
            }
        )

    for store_slug, changes in changes_by_store.items():
        catalog = await run_in_threadpool(db.get_catalog_items, store_slug, "WB")
        catalog_articles = {str(item.get("article") or "").strip() for item in catalog}
        missing = next(
            (change["article"] for change in changes if change["article"] not in catalog_articles),
            None,
        )
        if missing is not None:
            return JSONResponse(
                {"ok": False, "error": f"Товар {missing} не найден в актуальном каталоге"},
                status_code=404,
            )

    def submit() -> dict:
        reports: dict[str, dict] = {}
        accepted: list[dict] = []
        errors: list[dict] = []
        user = request.state.user
        for store_slug, changes in changes_by_store.items():
            try:
                report = unit_economics_1c_prices.submit_price_changes(store_slug, changes)
                if report.get("sent"):
                    report = unit_economics_1c_prices.finalize_price_change_report(
                        store_slug,
                        report,
                    )
            except Exception as error:
                message = getattr(error, "friendly", None) or str(error) or type(error).__name__
                logger.warning(
                    "unit_economics_1c_price_submit_failed store=%s error=%s",
                    store_slug,
                    message,
                )
                report = {
                    "ok": False,
                    "sent": 0,
                    "accepted": [],
                    "errors": [
                        {
                            "product_id": f"{store_slug}:{change['article']}",
                            "article": change["article"],
                            "error": message,
                        }
                        for change in changes
                    ],
                }
            reports[store_slug] = report
            accepted.extend(report.get("accepted") or [])
            errors.extend(report.get("errors") or [])
            if report.get("sent"):
                db.log_action(
                    int(user["id"]),
                    str(user["full_name"]),
                    "unit_economics_1c_price_submit",
                    (
                        f"Переданы цены WB: {STORES[store_slug]['name']}, "
                        f"товаров {report['sent']}, uploadID {report.get('upload_id')}"
                    ),
                    datetime.now(UTC).isoformat(),
                )
        sync_errors = [
            {
                "store_slug": store_slug,
                "error": str(
                    (report.get("sync") or {}).get("error") or "Не удалось автоматически обновить цены в БД"
                ),
            }
            for store_slug, report in reports.items()
            if report.get("sent") and not report.get("price_data_refreshed")
        ]
        price_data_refreshed = not sync_errors and any(report.get("sent") for report in reports.values())
        return {
            "ok": not errors and not sync_errors,
            "accepted_count": len(accepted),
            "accepted_product_ids": [item["product_id"] for item in accepted],
            "accepted": accepted,
            "errors": errors,
            "sync_errors": sync_errors,
            "price_data_refreshed": price_data_refreshed,
            "reports": reports,
        }

    return await run_in_threadpool(submit)


@router.post("/api/unit-economics-1c/prices/preview")
async def unit_economics_1c_prices_preview(
    request: Request,
    payload: UnitEconomics1CPriceChangeRequest,
):
    if not has_section_access(
        request.state.user,
        SectionName.UNIT_ECONOMICS_1C,
        SectionAccessLevel.WRITE,
    ):
        return JSONResponse({"ok": False, "error": "Нет права изменять цены"}, status_code=403)

    item = payload.data[0]
    store_slug = item.store_slug.lower()
    if store_slug not in STORES:
        return JSONResponse({"ok": False, "error": f"Кабинет {store_slug} не найден"}, status_code=404)
    if not has_store_access(request.state.user, store_slug):
        return JSONResponse(
            {"ok": False, "error": f"Нет доступа к кабинету {STORES[store_slug]['name']}"},
            status_code=403,
        )
    catalog = await run_in_threadpool(db.get_catalog_items, store_slug, "WB")
    if item.article not in {str(product.get("article") or "").strip() for product in catalog}:
        return JSONResponse(
            {"ok": False, "error": f"Товар {item.article} не найден в актуальном каталоге"},
            status_code=404,
        )
    change = {
        "article": item.article,
        "target_price": item.target_price,
        "target_kind": item.target_kind,
    }
    try:
        report = await run_in_threadpool(
            unit_economics_1c_prices.preview_price_changes,
            store_slug,
            [change],
        )
    except Exception as error:
        message = getattr(error, "friendly", None) or str(error) or type(error).__name__
        logger.warning(
            "unit_economics_1c_price_preview_failed store=%s error=%s",
            store_slug,
            message,
        )
        return JSONResponse({"ok": False, "error": message}, status_code=502)
    status_code = 200 if report.get("accepted") else 422
    return JSONResponse(report, status_code=status_code)


@router.put("/api/unit-economics-1c/product-settings/{store_slug}")
async def unit_economics_1c_product_settings_save(
    request: Request,
    store_slug: str,
    payload: UnitEconomics1CProductSettingsRequest,
):
    normalized_store = store_slug.lower()
    if normalized_store not in STORES:
        return JSONResponse({"ok": False, "error": "Кабинет не найден"}, status_code=404)
    if not has_store_access(request.state.user, normalized_store):
        return JSONResponse({"ok": False, "error": "Нет доступа к этому кабинету"}, status_code=403)
    if not has_section_access(
        request.state.user,
        SectionName.UNIT_ECONOMICS_1C,
        SectionAccessLevel.WRITE,
    ):
        return JSONResponse({"ok": False, "error": "Нет права изменять параметры"}, status_code=403)

    catalog = await run_in_threadpool(db.get_catalog_items, normalized_store, "WB")
    if payload.article not in {str(item.get("article") or "") for item in catalog}:
        return JSONResponse({"ok": False, "error": "Товар не найден в актуальном каталоге"}, status_code=404)

    updated_at = datetime.now(UTC).isoformat()
    user = request.state.user

    def save() -> dict:
        saved = db.save_unit_economics_1c_product_settings(
            normalized_store,
            payload,
            updated_at=updated_at,
            updated_by_user_id=int(user["id"]),
            updated_by_name=str(user["full_name"]),
        )
        cabinet = db.get_unit_economics_1c_cabinet_settings(normalized_store)
        acceptance = unit_economics_1c.calculate_paid_acceptance_cost(
            saved.volume_l,
            cabinet.acceptance_coefficient,
        )
        delivery = unit_economics_1c.calculate_delivery_with_returns(
            saved.delivery_wb_rub,
            saved.buyout_percent,
            saved.return_cost_rub,
            acceptance,
        )
        db.log_action(
            int(user["id"]),
            str(user["full_name"]),
            "unit_economics_1c_product_settings",
            f"Сохранены параметры товара {payload.article} ({STORES[normalized_store]['name']})",
            updated_at,
        )
        return {
            **saved.model_dump(mode="json"),
            "paid_acceptance_cost": acceptance,
            "delivery_with_returns": delivery,
        }

    return {"ok": True, "settings": await run_in_threadpool(save)}


@router.get("/sales/unit-economics-1c/cabinet-settings", response_class=HTMLResponse)
async def sales_unit_economics_1c_cabinet_settings(request: Request):
    store_slugs = accessible_store_slugs(request.state.user)
    settings = await run_in_threadpool(_cabinet_settings_payload, store_slugs)
    content = fill_template(
        "unit_economics_1c_cabinet_settings_content.html",
        cabinet_settings_config=json.dumps(
            {
                "marketplace": "WB",
                "canEdit": has_section_access(
                    request.state.user,
                    SectionName.UNIT_ECONOMICS_1C,
                    SectionAccessLevel.WRITE,
                ),
                "items": settings,
            },
            ensure_ascii=False,
        ).replace("</", "<\\/"),
    )
    return render_page(
        "CheckStock — Юнит-экономика 1С — Ввод данных по кабинетам",
        "unit_1c_settings",
        content,
        request.state.user,
        content_class="content--unit-1c-settings",
    )


@router.get("/api/unit-economics-1c/cabinet-settings")
async def unit_economics_1c_cabinet_settings_data(request: Request, marketplace: str = "WB"):
    if marketplace.upper() != "WB":
        return JSONResponse(
            {"ok": False, "error": "Пока поддерживаются только кабинеты WB"},
            status_code=400,
        )
    store_slugs = accessible_store_slugs(request.state.user)
    settings = await run_in_threadpool(_cabinet_settings_payload, store_slugs)
    return {"ok": True, "marketplace": "WB", "items": settings}


@router.post("/api/unit-economics-1c/source-data/sync")
async def unit_economics_1c_source_data_sync(request: Request):
    if not has_section_access(
        request.state.user,
        SectionName.UNIT_ECONOMICS_1C,
        SectionAccessLevel.WRITE,
    ):
        return JSONResponse(
            {"ok": False, "error": "Нет права запускать загрузку себестоимости"},
            status_code=403,
        )
    store_slugs = accessible_store_slugs(request.state.user)
    user = request.state.user

    def sync() -> dict:
        report = unit_economics_1c_source.sync_all()
        db.log_action(
            int(user["id"]),
            str(user["full_name"]),
            "unit_economics_1c_source_sync",
            f"Внепланово обновлены данные 1С из Google Sheets: {report['saved']} товаров",
            datetime.now(UTC).isoformat(),
        )
        return {
            "report": report,
            "items": _cabinet_settings_payload(store_slugs),
        }

    try:
        result = await run_in_threadpool(sync)
    except unit_economics_1c_source.SourceDataError as error:
        logger.warning("unit_economics_1c_manual_source_sync_failed error=%s", error)
        return JSONResponse(
            {"ok": False, "error": f"Не удалось загрузить данные из Google Sheets: {error}"},
            status_code=502,
        )
    return {"ok": True, **result}


@router.put("/api/unit-economics-1c/cabinet-settings/{store_slug}")
async def unit_economics_1c_cabinet_settings_save(
    request: Request,
    store_slug: str,
    payload: UnitEconomics1CCabinetSettingsWebRequest,
):
    normalized_store = store_slug.lower()
    if normalized_store not in STORES:
        return JSONResponse({"ok": False, "error": "Кабинет не найден"}, status_code=404)
    if not has_store_access(request.state.user, normalized_store):
        return JSONResponse(
            {"ok": False, "error": "Нет доступа к этому кабинету"},
            status_code=403,
        )
    updated_at = datetime.now(UTC).isoformat()
    user = request.state.user
    if normalized_store != "gogol" or payload.tax_system == "usn":
        effective_payload = payload.model_copy(update={"tax_system": "usn", "osno_percent": 0})
    else:
        effective_payload = payload.model_copy(update={"usn_percent": 0})

    def save() -> dict:
        settings = db.save_unit_economics_1c_cabinet_settings(
            normalized_store,
            effective_payload,
            updated_at=updated_at,
            updated_by_user_id=int(user["id"]),
            updated_by_name=str(user["full_name"]),
        )
        db.log_action(
            int(user["id"]),
            str(user["full_name"]),
            "unit_economics_1c_cabinet_settings",
            f"Сохранены параметры WB-кабинета {STORES[normalized_store]['name']}",
            updated_at,
        )
        return {
            **settings.model_dump(mode="json"),
            "store_name": STORES[normalized_store]["name"],
            "store_initials": STORES[normalized_store]["initials"],
            "store_color": STORES[normalized_store]["color"],
            "store_text": STORES[normalized_store]["text"],
        }

    settings = await run_in_threadpool(save)
    return {"ok": True, "settings": settings}


def _render_unit_economics_1c_placeholder(
    request: Request,
    *,
    title: str,
    active: str,
    logo: str,
    logo_class: str,
    heading: str,
) -> str:
    content = fill_template(
        "unit_economics_1c_placeholder_content.html",
        logo=logo,
        logo_class=logo_class,
        heading=heading,
    )
    return render_page(
        title,
        active,
        content,
        request.state.user,
        content_class="content--unit-1c",
    )


@router.get("/sales/unit-economics-1c/ozon", response_class=HTMLResponse)
async def sales_unit_economics_1c_ozon(request: Request):
    return _render_unit_economics_1c_placeholder(
        request,
        title="CheckStock — Юнит-экономика 1С — Ozon",
        active="unit_1c_ozon",
        logo="OZON",
        logo_class="ozon",
        heading="Раздел Ozon в разработке",
    )


@router.get("/sales/unit-economics-1c/yandex-market", response_class=HTMLResponse)
async def sales_unit_economics_1c_yandex(request: Request):
    return _render_unit_economics_1c_placeholder(
        request,
        title="CheckStock — Юнит-экономика 1С — Яндекс Маркет",
        active="unit_1c_yandex",
        logo="Я",
        logo_class="ym",
        heading="Раздел Яндекс Маркета в разработке",
    )
