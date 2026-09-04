"""WB target-price report with scoped per-product goal overrides."""

import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app import db
from app import unit_economics_1c as economics
from app import unit_economics_1c_target_price_export as target_export
from app import unit_economics_1c_target_prices as pricing
from app.access_control import accessible_stores, has_scope
from app.dto.identity import Role, SectionAccessLevel, SectionName, coerce_user
from app.dto.unit_economics_1c import (
    UnitEconomics1CProductTargetRequest,
    UnitEconomics1CTargetPriceExportRequest,
)
from app.section_access import has_access as has_section_access
from app.stores import STORES
from app.web.downloads import _download_headers
from app.web.routers.unit_economics import _manager_matches_user, _report_historical_economics
from app.web.templating import fill_template, render_page

router = APIRouter()


@router.get("/sales/unit-economics-1c/reports/target-price", response_class=HTMLResponse)
async def target_price_page(request: Request):
    stores = accessible_stores(request.state.user, "WB")
    content = fill_template(
        "unit_economics_1c_target_price_content.html",
        target_price_config=json.dumps(
            {
                "stores": [{"slug": slug, "name": STORES[slug]["name"]} for slug in stores],
                "canEdit": has_section_access(
                    request.state.user,
                    SectionName.UNIT_ECONOMICS_1C,
                    SectionAccessLevel.WRITE,
                ),
            },
            ensure_ascii=False,
        ).replace("</", "<\\/"),
    )
    return render_page(
        "CheckStock — Целевая цена",
        "unit_1c_target_price",
        content,
        request.state.user,
        content_class="content--unit-1c-report",
    )


@router.get("/api/unit-economics-1c/reports/target-price")
async def target_price_data(request: Request):
    accessible = accessible_stores(request.state.user, "WB")
    selected = str(request.query_params.get("store") or "").strip().lower()
    if selected and selected not in accessible:
        return JSONResponse({"ok": False, "error": "Нет доступа к кабинету"}, status_code=403)
    stores = (selected,) if selected else accessible
    user = coerce_user(request.state.user)

    def load():
        start, end = pricing.closed_week()
        weekly = pricing.load_weekly_metrics(stores, today=end + timedelta(days=1))
        cabinets = {item.store_slug: item for item in db.list_unit_economics_1c_cabinet_settings(stores)}
        period_metrics = economics.load_product_metrics(stores, period_days=7, today=end)
        daily_orders = {}
        for item in db.get_unit_economics_1c_funnel_daily_order_rows(
            stores,
            start.isoformat(),
            end.isoformat(),
        ):
            daily_orders.setdefault(
                (str(item["store_slug"]), str(item["article"])),
                {},
            )[str(item["day"])] = item
        margin_snapshots = {}
        for item in db.get_unit_economics_1c_daily_margin_snapshots(
            stores,
            start.isoformat(),
            end.isoformat(),
        ):
            margin_snapshots.setdefault(
                (str(item["store_slug"]), str(item["article"])),
                {},
            )[str(item["day"])] = item
        prices = {
            (str(item["store_slug"]), str(item["article"])): item
            for item in db.get_unit_economics_1c_latest_daily_prices(stores)
        }
        references = {
            (str(item["store_slug"]), str(item["article"])): item
            for item in db.get_unit_economics_1c_product_reference_rows(stores)
        }
        settings = {
            (item.store_slug, item.article): item
            for item in db.list_unit_economics_1c_product_settings(stores)
        }
        rows = []
        for slug in stores:
            for product in db.get_stock_items(slug, "WB"):
                article = str(product.get("article") or "")
                nm_id = article.partition(" / ")[0].strip()
                reference = references.get((slug, article), {})
                manager = str(reference.get("manager") or "")
                if user is None or (user.role == Role.USER and not _manager_matches_user(manager, user)):
                    continue
                product_metrics = period_metrics.get((slug, nm_id))
                if product_metrics is None:
                    product_metrics = economics.empty_product_metrics(period_days=7, today=end)
                product_metrics = economics.apply_buyout_default(
                    product_metrics,
                    cabinets[slug].default_buyout_percent,
                )
                product_weekly = dict(weekly[(slug, nm_id)])
                product_weekly.update(
                    {
                        "period_from": start.isoformat(),
                        "period_to": end.isoformat(),
                        "spend": round(float(product_metrics.get("spend") or 0), 2),
                        "orders_amount": round(
                            float(product_metrics.get("orders_amount") or 0),
                            2,
                        ),
                        "orders_count": int(product_metrics.get("orders_count") or 0),
                        "buyout_percent": float(product_metrics.get("buyout_percent") or 0),
                        "buyout_default_applied": bool(
                            product_metrics.get("buyout_default_applied")
                        ),
                        "drr": product_metrics.get("drr"),
                        "advertising_per_unit": product_metrics.get("spend_per_order"),
                    }
                )
                product_weekly["average_order_price"] = (
                    product_weekly["orders_amount"] / product_weekly["orders_count"]
                    if product_weekly["orders_count"] > 0
                    and product_weekly["orders_amount"] > 0
                    else None
                )
                daily_advertising = {
                    str(item.get("date")): max(
                        float(item.get("advertising_spend") or 0),
                        0.0,
                    )
                    for item in product_metrics.get("daily") or []
                    if isinstance(item, dict) and item.get("date")
                }
                historical = _report_historical_economics(
                    date_from=start,
                    date_to=end,
                    daily_orders=daily_orders.get((slug, nm_id), {}),
                    margin_snapshots=margin_snapshots.get((slug, article), {}),
                    live_day=end + timedelta(days=1),
                    live_unit_margin=None,
                    live_purchase_price=None,
                    daily_advertising=daily_advertising,
                    fallback_buyout_percent=product_metrics.get("buyout_percent"),
                    allow_partial=True,
                )
                rows.append(
                    {
                        "store_slug": slug,
                        "store_name": STORES[slug]["name"],
                        "article": article,
                        "name": str(product.get("name") or article),
                        "manager": manager,
                        "image_url": str(product.get("image_url") or ""),
                        **pricing.calculate_row(
                            price=prices.get((slug, article), {}),
                            reference=reference,
                            product_settings=settings.get((slug, article)),
                            cabinet=cabinets[slug],
                            weekly=product_weekly,
                            current_roi=historical.get("roi"),
                        ),
                    }
                )
        return {"ok": True, "period_from": start.isoformat(), "period_to": end.isoformat(), "rows": rows}

    return await run_in_threadpool(load)


async def _target_product_error(request: Request, store_slug: str, article: str):
    if store_slug not in STORES:
        return JSONResponse({"ok": False, "error": "Кабинет не найден"}, status_code=404)
    if not has_scope(request.state.user, store_slug, "WB"):
        return JSONResponse({"ok": False, "error": "Нет доступа к этому кабинету"}, status_code=403)
    if not has_section_access(
        request.state.user,
        SectionName.UNIT_ECONOMICS_1C,
        SectionAccessLevel.WRITE,
    ):
        return JSONResponse({"ok": False, "error": "Нет права изменять цели"}, status_code=403)
    catalog = await run_in_threadpool(db.get_catalog_items, store_slug, "WB")
    if article not in {str(item.get("article") or "") for item in catalog}:
        return JSONResponse({"ok": False, "error": "Товар не найден в актуальном каталоге"}, status_code=404)
    user = coerce_user(request.state.user)
    if user is not None and user.role == Role.USER:
        references = await run_in_threadpool(
            db.get_unit_economics_1c_product_reference_rows, (store_slug,)
        )
        manager = next(
            (str(item.get("manager") or "") for item in references if str(item.get("article") or "") == article),
            "",
        )
        if not _manager_matches_user(manager, user):
            return JSONResponse({"ok": False, "error": "Нет доступа к этому товару"}, status_code=403)
    return None


def _save_product_targets(request: Request, store_slug: str, article: str,
                          target_drr_percent: float | None,
                          target_roi_percent: float | None):
    updated_at = datetime.now(UTC).isoformat()
    user = request.state.user
    saved = db.save_unit_economics_1c_product_targets(
        store_slug,
        article,
        target_drr_percent,
        target_roi_percent,
        updated_at=updated_at,
        updated_by_user_id=int(user["id"]),
        updated_by_name=str(user["full_name"]),
    )
    action = "Сброшены цели товара" if target_drr_percent is None else "Сохранены цели товара"
    db.log_action(
        int(user["id"]),
        str(user["full_name"]),
        "unit_economics_1c_product_targets",
        f"{action} {article} ({STORES[store_slug]['name']})",
        updated_at,
    )
    return saved


@router.put("/api/unit-economics-1c/reports/target-price/{store_slug}/targets")
async def target_price_product_targets_save(
    request: Request,
    store_slug: str,
    payload: UnitEconomics1CProductTargetRequest,
):
    normalized_store = store_slug.strip().lower()
    error = await _target_product_error(request, normalized_store, payload.article)
    if error is not None:
        return error
    saved = await run_in_threadpool(
        _save_product_targets,
        request,
        normalized_store,
        payload.article,
        payload.target_drr_percent,
        payload.target_roi_percent,
    )
    return {"ok": True, "settings": saved.model_dump(mode="json")}


@router.delete("/api/unit-economics-1c/reports/target-price/{store_slug}/targets")
async def target_price_product_targets_reset(request: Request, store_slug: str, article: str):
    normalized_store = store_slug.strip().lower()
    normalized_article = article.strip()
    error = await _target_product_error(request, normalized_store, normalized_article)
    if error is not None:
        return error
    saved = await run_in_threadpool(
        _save_product_targets,
        request,
        normalized_store,
        normalized_article,
        None,
        None,
    )
    return {"ok": True, "settings": saved.model_dump(mode="json")}


@router.post("/api/unit-economics-1c/reports/target-price.xlsx")
async def target_price_xlsx(
    request: Request,
    payload: UnitEconomics1CTargetPriceExportRequest,
):
    allowed = set(accessible_stores(request.state.user, "WB"))
    keys = {key for key, _ in target_export.COLUMNS}
    rows = []
    for item in payload.rows:
        if item.store_slug not in allowed:
            continue
        rows.append({key: item.get(key) for key in keys})
    content, filename = await run_in_threadpool(
        target_export.build_xlsx,
        rows,
        str(payload.period_from or ""),
        str(payload.period_to or ""),
    )
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )
