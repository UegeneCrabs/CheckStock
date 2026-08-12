import logging
from datetime import UTC, datetime, timedelta

from app import db
from app.stores import STORES
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens

logger = logging.getLogger(__name__)

INITIAL_ORDER_LOOKBACK_DAYS = 30
ORDER_SYNC_OVERLAP_DAYS = 1
BUYER_PRICE_MAX_AGE_DAYS = 7


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _base_article(article: str) -> tuple[str, str]:
    base, separator, size = str(article or "").partition(" / ")
    return base.strip(), size.strip() if separator else ""


def _number(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _friendly_error(label: str, error: Exception) -> str:
    if isinstance(error, wb_api.WBApiError):
        return f"{label}: {error.friendly}"
    return f"{label}: {error}"


def _price_map(rows: list[dict]) -> dict[tuple[str, str], dict]:

    prices: dict[tuple[str, str], dict] = {}

    for row in rows:
        nm_id = str(row.get("nmID") or row.get("nmId") or "").strip()
        if not nm_id:
            continue

        sizes = row.get("sizes")
        if not isinstance(sizes, list) or not sizes:
            sizes = [row]

        for size in sizes:
            tech_size = str(
                size.get("techSizeName") or size.get("techSize") or row.get("techSizeName") or ""
            ).strip()
            entry = {
                "list_price": _number(size.get("price")),
                "discounted_price": _number(size.get("discountedPrice")),
                "club_discounted_price": _number(size.get("clubDiscountedPrice")),
            }
            if not any(value and value > 0 for value in entry.values()):
                continue
            prices[(nm_id, tech_size)] = entry

            current = prices.get((nm_id, ""))
            candidate = entry.get("discounted_price") or entry.get("list_price")
            current_value = (current or {}).get("discounted_price") or (current or {}).get("list_price")
            if current is None or (candidate and (not current_value or candidate < current_value)):
                prices[(nm_id, "")] = entry

    return prices


def _order_price_maps(orders: list[dict]) -> tuple[dict[str, dict], dict[tuple[str, str], dict]]:
    by_barcode: dict[str, dict] = {}
    by_article: dict[tuple[str, str], dict] = {}

    for order in orders:
        price = _number(order.get("finishedPrice"))
        if price is None or price <= 0:
            continue
        observed_at = str(order.get("lastChangeDate") or order.get("date") or "")
        entry = {
            "buyer_price": price,
            "spp_percent": _number(order.get("spp")),
            "buyer_price_observed_at": observed_at,
            "discounted_price": _number(order.get("priceWithDisc")),
        }
        barcode = str(order.get("barcode") or "").strip()
        nm_id = str(order.get("nmId") or order.get("nmID") or "").strip()
        tech_size = str(order.get("techSize") or "").strip()

        def newer(current: dict | None, candidate_observed_at: str = observed_at) -> bool:
            return current is None or candidate_observed_at >= str(
                current.get("buyer_price_observed_at") or ""
            )

        if barcode and newer(by_barcode.get(barcode)):
            by_barcode[barcode] = entry
        key = (nm_id, tech_size)
        if nm_id and newer(by_article.get(key)):
            by_article[key] = entry
        fallback = (nm_id, "")
        if nm_id and newer(by_article.get(fallback)):
            by_article[fallback] = entry

    return by_barcode, by_article


def _order_date_from(store_slug: str, force_full: bool = False) -> str:
    now = datetime.now(UTC)
    last_sync = None if force_full else _parse_datetime(db.get_wb_price_last_sync(store_slug))
    start = (
        last_sync - timedelta(days=ORDER_SYNC_OVERLAP_DAYS)
        if last_sync
        else now - timedelta(days=INITIAL_ORDER_LOOKBACK_DAYS)
    )
    earliest = now - timedelta(days=INITIAL_ORDER_LOOKBACK_DAYS)
    return max(start, earliest).strftime("%Y-%m-%dT%H:%M:%S")


def sync_prices_store(store_slug: str, force_full: bool = False) -> dict:

    token = wb_tokens.get_token(store_slug)
    stock_rows = db.get_stock_items(store_slug, "WB", ("fbs", "fbo"))
    nm_ids = sorted(
        {int(base) for row in stock_rows for base, _ in [_base_article(row["article"])] if base.isdigit()}
    )
    warnings: list[str] = []
    price_map: dict[tuple[str, str], dict] = {}
    orders_by_barcode: dict[str, dict] = {}
    orders_by_article: dict[tuple[str, str], dict] = {}
    prices_ok = False
    orders_ok = False

    try:
        price_map = _price_map(wb_api.get_products_with_prices(token, nm_ids))
        prices_ok = True
    except Exception as error:
        warnings.append(_friendly_error("Цены продавца", error))

    try:
        orders = wb_api.get_orders(token, _order_date_from(store_slug, force_full))
        orders_by_barcode, orders_by_article = _order_price_maps(orders)
        orders_ok = True
    except Exception as error:
        warnings.append(_friendly_error("Цена с СПП из заказов", error))

    if not prices_ok and not orders_ok:
        raise RuntimeError("; ".join(warnings) or "WB не вернул цены")

    entries = []
    observed_count = 0
    for stock in stock_rows:
        base, size = _base_article(stock["article"])
        seller = price_map.get((base, size)) or price_map.get((base, "")) or {}
        buyer = (
            orders_by_barcode.get(str(stock.get("barcode") or "").strip())
            or orders_by_article.get((base, size))
            or orders_by_article.get((base, ""))
            or {}
        )
        if buyer:
            observed_count += 1
        entries.append(
            {
                "article": stock["article"],
                "nm_id": int(base) if base.isdigit() else None,
                "tech_size": size,
                **seller,
                **buyer,
            }
        )

    updated_at = _now_iso()
    count = db.upsert_wb_unit_prices(store_slug, entries, updated_at)
    return {
        "rows": count,
        "buyer_prices": observed_count,
        "seller_prices": len(price_map),
        "warnings": warnings,
        "updated_at": updated_at,
    }


def sync_reference_store(store_slug: str) -> dict:

    token = wb_tokens.get_token(store_slug)
    stock_rows = db.get_stock_items(store_slug, "WB", ("fbs", "fbo"))
    cards = wb_api.get_cards_list(token)
    cards_by_nm = {
        str(card.get("nmID") or "").strip(): card for card in cards if str(card.get("nmID") or "").strip()
    }
    commissions = {
        int(row["subjectID"]): float(row["kgvpSupplier"])
        for row in wb_api.get_category_commissions(token)
        if row.get("subjectID") is not None and row.get("kgvpSupplier") is not None
    }

    entries = []
    with_volume = 0
    with_commission = 0
    for stock in stock_rows:
        base, size = _base_article(stock["article"])
        card = cards_by_nm.get(base) or {}
        dimensions = card.get("dimensions") or {}
        length = _number(dimensions.get("length"))
        width = _number(dimensions.get("width"))
        height = _number(dimensions.get("height"))
        volume = length * width * height / 1000 if length and width and height else None
        subject_id = card.get("subjectID")
        commission = commissions.get(subject_id)
        if volume:
            with_volume += 1
        if commission is not None:
            with_commission += 1
        entries.append(
            {
                "article": stock["article"],
                "nm_id": int(base) if base.isdigit() else None,
                "tech_size": size,
                "subject_id": subject_id,
                "category": str(card.get("subjectName") or "").strip(),
                "length_cm": length,
                "width_cm": width,
                "height_cm": height,
                "volume_l": volume,
                "weight_kg": _number(dimensions.get("weightBrutto")),
                "commission_fbs_rate": commission,
            }
        )

    updated_at = _now_iso()
    count = db.upsert_wb_unit_references(store_slug, entries, updated_at)
    return {
        "rows": count,
        "with_volume": with_volume,
        "with_commission": with_commission,
        "updated_at": updated_at,
    }


def _sync_all(sync_store, health_scope: str) -> dict:
    report: dict[str, dict] = {}
    for store_slug in STORES:
        if not wb_tokens.has_token(store_slug):
            continue
        try:
            result = sync_store(store_slug)
            report[store_slug] = {"ok": True, **result}
            db.record_sync_health(store_slug, "WB", health_scope, True, None, result["updated_at"])
        except Exception as error:
            message = _friendly_error("WB", error)
            logger.exception(
                "Юнит-экономика WB %s (%s): %s",
                store_slug,
                health_scope,
                message,
            )
            report[store_slug] = {"ok": False, "error": message}
            db.record_sync_health(store_slug, "WB", health_scope, False, message, _now_iso())
    return report


def sync_prices_all() -> dict:
    return _sync_all(sync_prices_store, "unit_prices")


def sync_references_all() -> dict:
    return _sync_all(sync_reference_store, "unit_reference")


def _fresh_buyer_price(metric: dict) -> bool:
    price = _number(metric.get("buyer_price"))
    observed_at = _parse_datetime(metric.get("buyer_price_observed_at"))
    return bool(
        price and observed_at and observed_at >= datetime.now(UTC) - timedelta(days=BUYER_PRICE_MAX_AGE_DAYS)
    )


def load_wb_fbs_data(store_slug: str) -> dict:

    stock_rows = db.get_stock_items(store_slug, "WB", ("fbs", "fbo"))
    metrics = db.get_wb_unit_metrics(store_slug)
    costs = db.get_unit_costs(store_slug)
    rows = []
    fallback_prices = 0
    missing_prices = 0
    missing_costs = 0
    missing_volumes = 0
    missing_commissions = 0

    for stock in stock_rows:
        article = stock["article"]
        base, _ = _base_article(article)
        metric = metrics.get(article) or {}
        cost = costs.get(base) or {}
        has_buyer_price = _fresh_buyer_price(metric)
        price = metric.get("buyer_price") if has_buyer_price else metric.get("discounted_price")
        price_source = "finishedPrice" if has_buyer_price else ("discountedPrice" if price else "")
        if price_source == "discountedPrice":
            fallback_prices += 1
        elif not price_source:
            missing_prices += 1
        if cost.get("purchase_price") is None:
            missing_costs += 1
        if metric.get("volume_l") is None:
            missing_volumes += 1
        if metric.get("commission_fbs_rate") is None:
            missing_commissions += 1

        rows.append(
            {
                "article": article,
                "name": stock["name"],
                "category": metric.get("category") or "",
                "price": price,
                "price_source": price_source,
                "buyer_price_observed_at": metric.get("buyer_price_observed_at"),
                "spp_percent": metric.get("spp_percent"),
                "purchase_price": cost.get("purchase_price"),
                "cost_updated_at": cost.get("updated_at"),
                "volume_l": metric.get("volume_l"),
                "length_cm": metric.get("length_cm"),
                "width_cm": metric.get("width_cm"),
                "height_cm": metric.get("height_cm"),
                "weight_kg": metric.get("weight_kg"),
                "commission_rate": metric.get("commission_fbs_rate"),
                "fbs_stock": int(stock.get("fbs_stock") or 0),
                "fbo_stock": int(stock.get("fbo_stock") or 0),
            }
        )

    warnings = []
    if fallback_prices:
        warnings.append(f"для {fallback_prices} товаров нет свежего заказа: показана цена продавца без СПП")
    if missing_prices:
        warnings.append(f"нет цены: {missing_prices}")
    if missing_costs:
        warnings.append(f"нет себестоимости в таблице 1С: {missing_costs}")
    if missing_volumes:
        warnings.append(f"нет корректных габаритов WB: {missing_volumes}")
    if missing_commissions:
        warnings.append(f"нет комиссии категории WB: {missing_commissions}")

    return {
        "ok": True,
        "store": store_slug,
        "rows": rows,
        "warnings": warnings,
        "updated_at": _now_iso(),
        "cached": False,
    }
