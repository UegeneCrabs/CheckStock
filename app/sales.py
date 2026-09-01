from __future__ import annotations

import json
import logging
import zipfile
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO
from xml.sax.saxutils import escape

from app import db
from app.config import settings
from app.domain import MARKETPLACES, MOSCOW_TIMEZONE
from app.ozon import api as ozon_api
from app.ozon import tokens as ozon_tokens
from app.stores import STORES
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens
from app.yandex import api as yandex_api
from app.yandex import tokens as yandex_tokens

logger = logging.getLogger(__name__)
MOSCOW = MOSCOW_TIMEZONE
MAX_RANGE_DAYS = {"WB": 90, "OZON": 365, "YANDEX MARKET": 365}
SOURCE_WINDOW_DAYS = {"WB": 90, "OZON": 30, "YANDEX MARKET": 30}
INITIAL_LOOKBACK_DAYS = {"WB": 90, "OZON": 365, "YANDEX MARKET": 365}


REFRESH_LOOKBACK_DAYS = 2

WB_FBS_CANCELLED_SUPPLIER_STATUSES = {"cancel"}
WB_FBS_CANCELLED_STATUSES = {
    "canceled",
    "canceled_by_client",
    "canceled_by_missed_call",
    "declined_by_client",
    "defect",
}
WB_FBS_SOLD_STATUSES = {"sold"}


def _now_iso() -> str:
    return datetime.now(MOSCOW).isoformat(timespec="seconds")


def _number(value, default: float = 0.0) -> float:
    if isinstance(value, dict):
        value = value.get("value", value.get("amount", default))
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return default


def _integer(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _timestamp(value, fallback: datetime | None = None) -> str:

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        raw = str(value or "").strip()
        parsed = None
        if raw:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                for pattern in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y", "%Y-%m-%d"):
                    try:
                        parsed = datetime.strptime(raw, pattern)
                        break
                    except ValueError:
                        continue
        if parsed is None:
            parsed = fallback or datetime.now(MOSCOW)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MOSCOW)
    return parsed.astimezone(MOSCOW).isoformat(timespec="seconds")


def _compact_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _windows(start: date, end: date, days: int = 30) -> Iterable[tuple[date, date]]:

    cursor = start
    while cursor < end:
        next_cursor = min(cursor + timedelta(days=days), end)
        yield cursor, next_cursor
        cursor = next_cursor


def _wb_sale_totals(rows: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for row in rows:
        srid = str(row.get("srid") or "").strip()
        if not srid:
            continue
        amount = _number(
            row.get("finishedPrice"),
            _number(row.get("priceWithDisc"), _number(row.get("totalPrice"))),
        )
        sale_id = str(row.get("saleID") or "").upper()
        is_return = sale_id.startswith("R") or row.get("isRealization") is False
        sign = -1 if is_return else 1
        current = result.setdefault(
            srid,
            {
                "amount": 0.0,
                "quantity": 0,
                "sold_at": "",
                "return_amount": 0.0,
                "return_quantity": 0,
                "returned_at": "",
            },
        )
        current["amount"] += sign * amount
        current["quantity"] += sign
        if sign > 0:
            sold_at = _timestamp(row.get("date") or row.get("lastChangeDate"))
            if not current["sold_at"] or sold_at < current["sold_at"]:
                current["sold_at"] = sold_at
        else:
            returned_at = _timestamp(row.get("date") or row.get("lastChangeDate"))
            current["return_amount"] += amount
            current["return_quantity"] += 1
            if not current["returned_at"] or returned_at < current["returned_at"]:
                current["returned_at"] = returned_at
    for value in result.values():
        value["amount"] = max(round(value["amount"], 2), 0.0)
        value["quantity"] = max(value["quantity"], 0)
        value["return_amount"] = round(value["return_amount"], 2)
        if not value["quantity"]:
            value["sold_at"] = ""
    return result


def _normalize_wb(
    store_slug: str,
    orders: list[dict],
    sales_rows: list[dict],
) -> list[dict]:
    sale_totals = _wb_sale_totals(sales_rows)
    lines: list[dict] = []
    for index, row in enumerate(orders):
        srid = str(row.get("srid") or "").strip()
        order_key = srid or ":".join(str(row.get(field) or "") for field in ("gNumber", "barcode", "date"))
        if not order_key:
            order_key = f"wb-row-{index}"
        amount = _number(
            row.get("finishedPrice"),
            _number(row.get("priceWithDisc"), _number(row.get("totalPrice"))),
        )
        cancelled = bool(row.get("isCancel"))
        sale = sale_totals.get(srid) or {}
        warehouse_type = str(row.get("warehouseType") or "").casefold()
        lines.append(
            {
                "store_slug": store_slug,
                "marketplace": "WB",
                "order_key": order_key,
                "line_key": str(row.get("barcode") or row.get("nmId") or index),
                "external_order_id": str(row.get("gNumber") or order_key),
                "scheme": "fbs" if any(word in warehouse_type for word in ("продав", "постав")) else "fbo",
                "status": "cancelled" if cancelled else "ordered",
                "substatus": "",
                "article": str(row.get("nmId") or ""),
                "barcode": str(row.get("barcode") or ""),
                "name": str(row.get("subject") or row.get("category") or ""),
                "ordered_at": _timestamp(row.get("date") or row.get("lastChangeDate")),
                "source_updated_at": _timestamp(row.get("lastChangeDate") or row.get("date")),
                "cancelled_at": _timestamp(row.get("cancelDate")) if cancelled else None,
                "sold_at": sale.get("sold_at") or None,
                "returned_at": sale.get("returned_at") or None,
                "quantity": 1,
                "cancelled_quantity": 1 if cancelled else 0,
                "sold_quantity": min(_integer(sale.get("quantity")), 1),
                "return_quantity": _integer(sale.get("return_quantity")),
                "order_amount": round(amount, 2),
                "cancelled_amount": round(amount, 2) if cancelled else 0.0,
                "sale_amount": round(_number(sale.get("amount")), 2),
                "return_amount": round(_number(sale.get("return_amount")), 2),
                "currency": "RUB",
                "raw_json": _compact_json(row),
            }
        )
    return lines


def _wb_fbs_amount(order: dict) -> float:
    """Return the buyer price in the seller's currency from WB's amount x100 fields."""

    for field in ("convertedFinalPrice", "convertedPrice", "finalPrice", "price"):
        value = order.get(field)
        if value is not None:
            return round(_number(value) / 100, 2)
    return 0.0


def _normalize_wb_fbs(
    store_slug: str,
    orders: list[dict],
    statuses: dict[int, dict],
) -> list[dict]:
    lines: list[dict] = []
    for index, order in enumerate(orders):
        order_id = _integer(order.get("id"))
        current_status = statuses.get(order_id) or {}
        supplier_status = str(current_status.get("supplierStatus") or "").strip().casefold()
        wb_status = str(current_status.get("wbStatus") or "").strip().casefold()
        cancelled = (
            supplier_status in WB_FBS_CANCELLED_SUPPLIER_STATUSES
            or wb_status in WB_FBS_CANCELLED_STATUSES
        )
        sold = wb_status in WB_FBS_SOLD_STATUSES and not cancelled
        skus = [str(value).strip() for value in order.get("skus") or () if str(value).strip()]
        barcode = skus[0] if skus else ""
        order_key = str(order.get("rid") or order_id or f"wb-fbs-row-{index}")
        line_key = barcode or str(order.get("nmId") or order_id or index)
        amount = _wb_fbs_amount(order)
        raw = dict(order)
        raw["supplierStatus"] = supplier_status
        raw["wbStatus"] = wb_status
        ordered_at = _timestamp(order.get("createdAt"))
        lines.append(
            {
                "store_slug": store_slug,
                "marketplace": "WB",
                "order_key": order_key,
                "line_key": line_key,
                "external_order_id": str(order.get("orderUid") or order_id or order_key),
                "scheme": "fbs",
                "status": supplier_status,
                "substatus": wb_status,
                "article": str(order.get("nmId") or ""),
                "barcode": barcode,
                "name": "",
                "ordered_at": ordered_at,
                "source_updated_at": ordered_at,
                "cancelled_at": None,
                "sold_at": None,
                "returned_at": None,
                "quantity": 1,
                "cancelled_quantity": 1 if cancelled else 0,
                "sold_quantity": 1 if sold else 0,
                "return_quantity": 0,
                "order_amount": amount,
                "cancelled_amount": amount if cancelled else 0.0,
                "sale_amount": amount if sold else 0.0,
                "return_amount": 0.0,
                "currency": "RUB",
                "raw_json": _compact_json(raw),
            }
        )
    return lines


def _merge_wb_lines(statistics_lines: list[dict], fbs_lines: list[dict]) -> list[dict]:
    """Enrich statistics rows with Marketplace statuses and retain FBS-only orders."""

    merged = list(statistics_lines)
    by_order_key = {str(line.get("order_key") or ""): line for line in merged}
    for fbs_line in fbs_lines:
        existing = by_order_key.get(str(fbs_line.get("order_key") or ""))
        if existing is None:
            merged.append(fbs_line)
            by_order_key[str(fbs_line.get("order_key") or "")] = fbs_line
            continue

        existing["scheme"] = "fbs"
        existing["status"] = fbs_line["status"]
        existing["substatus"] = fbs_line["substatus"]
        for field in ("external_order_id", "article", "barcode", "name"):
            if not existing.get(field) and fbs_line.get(field):
                existing[field] = fbs_line[field]
        existing["cancelled_quantity"] = max(
            _integer(existing.get("cancelled_quantity")),
            _integer(fbs_line.get("cancelled_quantity")),
        )
        existing["sold_quantity"] = max(
            _integer(existing.get("sold_quantity")),
            _integer(fbs_line.get("sold_quantity")),
        )
        existing["cancelled_amount"] = max(
            _number(existing.get("cancelled_amount")),
            _number(fbs_line.get("cancelled_amount")),
        )
        existing["sale_amount"] = max(
            _number(existing.get("sale_amount")),
            _number(fbs_line.get("sale_amount")),
        )
    return merged


def _ozon_financial_products(posting: dict) -> dict[str, dict]:
    financial = posting.get("financial_data") or {}
    rows = financial.get("products") or posting.get("financial_products") or []
    result = {}
    for row in rows:
        key = str(row.get("product_id") or row.get("sku") or row.get("offer_id") or "")
        if key:
            result[key] = row
    return result


def _normalize_ozon(store_slug: str, postings: list[dict], scheme: str) -> list[dict]:
    lines: list[dict] = []
    for posting_index, posting in enumerate(postings):
        order_key = str(
            posting.get("posting_number")
            or posting.get("order_number")
            or posting.get("order_id")
            or f"ozon-row-{posting_index}"
        )
        external_order_id = str(posting.get("order_number") or posting.get("order_id") or order_key)
        status = str(posting.get("status") or "").lower()
        substatus = str(posting.get("substatus") or "")
        cancellation = posting.get("cancellation") or {}
        cancelled = "cancel" in status or bool(cancellation.get("cancel_reason_id"))
        delivered = status in {"delivered", "completed"}
        ordered_at = _timestamp(
            posting.get("created_at") or posting.get("in_process_at") or posting.get("shipment_date")
        )
        updated_at = _timestamp(posting.get("updated_at") or posting.get("status_updated_at") or ordered_at)
        analytics = posting.get("analytics_data") or {}
        sold_source = (
            posting.get("delivering_date")
            or analytics.get("delivery_date_end")
            or posting.get("shipment_date")
            or updated_at
        )
        financial_by_product = _ozon_financial_products(posting)
        products = posting.get("products") or posting.get("items") or []
        for line_index, product in enumerate(products):
            product_key = str(
                product.get("sku") or product.get("product_id") or product.get("offer_id") or line_index
            )
            financial = financial_by_product.get(product_key) or {}
            quantity = max(_integer(product.get("quantity"), 1), 1)
            unit_price = _number(
                product.get("price"),
                _number(financial.get("price"), _number(financial.get("old_price"))),
            )
            amount = round(unit_price * quantity, 2)
            lines.append(
                {
                    "store_slug": store_slug,
                    "marketplace": "OZON",
                    "order_key": order_key,
                    "line_key": product_key,
                    "external_order_id": external_order_id,
                    "scheme": scheme,
                    "status": status,
                    "substatus": substatus,
                    "article": str(product.get("offer_id") or product.get("product_id") or ""),
                    "barcode": str(product.get("barcode") or ""),
                    "name": str(product.get("name") or ""),
                    "ordered_at": ordered_at,
                    "source_updated_at": updated_at,
                    "cancelled_at": updated_at if cancelled else None,
                    "sold_at": _timestamp(sold_source) if delivered else None,
                    "quantity": quantity,
                    "cancelled_quantity": quantity if cancelled else 0,
                    "sold_quantity": quantity if delivered and not cancelled else 0,
                    "order_amount": amount,
                    "cancelled_amount": amount if cancelled else 0.0,
                    "sale_amount": amount if delivered and not cancelled else 0.0,
                    "currency": str(product.get("currency_code") or "RUB"),
                    "raw_json": _compact_json(posting),
                }
            )
    return lines


def _money_value(value) -> float:
    return _number(value)


def _yandex_item_amount(item: dict) -> float:
    prices = item.get("prices") or {}
    amount = _money_value(prices.get("payment")) + _money_value(prices.get("cashback"))
    if amount:
        return amount
    quantity = max(_integer(item.get("count"), 1), 1)
    return _number(item.get("buyerPrice"), _number(item.get("price"))) * quantity


def _yandex_status_counts(item: dict) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in item.get("itemStatuses") or []:
        if not isinstance(value, dict):
            continue
        status = str(value.get("status") or "").upper()
        result[status] = result.get(status, 0) + _integer(value.get("count"))
    return result


def _normalize_yandex(store_slug: str, orders: list[dict]) -> list[dict]:
    lines: list[dict] = []
    for order_index, order in enumerate(orders):
        order_key = str(order.get("orderId") or order.get("id") or f"ym-row-{order_index}")
        status = str(order.get("status") or "").upper()
        substatus = str(order.get("substatus") or "")
        program = str(order.get("programType") or "FBS").upper()
        ordered_at = _timestamp(order.get("creationDate") or order.get("createdAt"))
        updated_at = _timestamp(order.get("updateDate") or order.get("updatedAt") or ordered_at)
        delivery = order.get("delivery") or {}
        delivery_dates = delivery.get("dates") or {}
        delivered_at = delivery_dates.get("realDeliveryDate") or updated_at
        order_cancelled = status == "CANCELLED"
        order_delivered = status in {"DELIVERED", "DELIVERED_TO_BUYER", "COMPLETED"}
        for line_index, item in enumerate(order.get("items") or []):
            quantity = max(_integer(item.get("count"), 1), 1)
            item_counts = _yandex_status_counts(item)
            cancelled_quantity = (
                quantity
                if order_cancelled
                else min(item_counts.get("CANCELLED", 0) + item_counts.get("REJECTED", 0), quantity)
            )
            sold_quantity = min(item_counts.get("DELIVERED_TO_BUYER", 0), quantity)
            if not item_counts and order_delivered:
                sold_quantity = quantity
            sold_quantity = max(min(sold_quantity, quantity - cancelled_quantity), 0)
            amount = round(_yandex_item_amount(item), 2)
            cancelled_amount = round(amount * cancelled_quantity / quantity, 2)
            sale_amount = round(amount * sold_quantity / quantity, 2)
            line_key = str(item.get("id") or item.get("marketSku") or item.get("offerId") or line_index)
            lines.append(
                {
                    "store_slug": store_slug,
                    "marketplace": "YANDEX MARKET",
                    "order_key": order_key,
                    "line_key": line_key,
                    "external_order_id": order_key,
                    "scheme": "fbo" if program in {"FBY", "FBO"} else "fbs",
                    "status": status,
                    "substatus": substatus,
                    "article": str(item.get("offerId") or item.get("shopSku") or ""),
                    "barcode": str(item.get("barcode") or ""),
                    "name": str(item.get("offerName") or item.get("name") or ""),
                    "ordered_at": ordered_at,
                    "source_updated_at": updated_at,
                    "cancelled_at": updated_at if cancelled_quantity else None,
                    "sold_at": _timestamp(delivered_at) if sold_quantity else None,
                    "quantity": quantity,
                    "cancelled_quantity": cancelled_quantity,
                    "sold_quantity": sold_quantity,
                    "order_amount": amount,
                    "cancelled_amount": cancelled_amount,
                    "sale_amount": sale_amount,
                    "currency": "RUB",
                    "raw_json": _compact_json(order),
                }
            )
    return lines


def _load_wb_fbs_lines(
    store_slug: str,
    token: str,
    start: date,
    end: date,
) -> tuple[list[dict], list[str]]:
    orders_by_id: dict[int, dict] = {}
    warnings: list[str] = []
    for window_start, window_end in _windows(start, end, 30):
        date_from = int(datetime.combine(window_start, time.min, tzinfo=MOSCOW).timestamp())
        date_to = int(datetime.combine(window_end, time.min, tzinfo=MOSCOW).timestamp()) - 1
        try:
            for order in wb_api.get_fbs_orders(token, date_from, date_to):
                order_id = _integer(order.get("id"))
                if order_id:
                    orders_by_id[order_id] = order
        except Exception as exc:
            warnings.append(
                f"FBS {window_start:%d.%m}-{window_end - timedelta(days=1):%d.%m}: "
                f"{type(exc).__name__}: {exc}"
            )

    statuses: dict[int, dict] = {}
    if orders_by_id:
        try:
            statuses = wb_api.get_fbs_order_statuses(token, list(orders_by_id))
        except Exception as exc:
            warnings.append(f"FBS статусы: {type(exc).__name__}: {exc}")
    return _normalize_wb_fbs(store_slug, list(orders_by_id.values()), statuses), warnings


def _sync_wb(store_slug: str, start: date, end: date) -> tuple[list[dict], list[str]]:
    token = wb_tokens.get_token(store_slug)
    date_from = datetime.combine(start, time.min).isoformat(timespec="seconds")
    orders = wb_api.get_orders(token, date_from, max_pages=settings.wb_sales_max_pages)
    warnings: list[str] = []
    fbs_lines, fbs_warnings = _load_wb_fbs_lines(store_slug, token, start, end)
    warnings.extend(fbs_warnings)
    try:
        sales_rows = wb_api.get_sales(token, date_from, max_pages=settings.wb_sales_max_pages)
    except Exception as exc:
        sales_rows = []
        warnings.append(f"продажи: {type(exc).__name__}: {exc}")
    statistics_lines = _normalize_wb(store_slug, orders, sales_rows)
    return _merge_wb_lines(statistics_lines, fbs_lines), warnings


def _sync_ozon(store_slug: str, start: date, end: date) -> tuple[list[dict], list[str]]:
    client_id, api_key = ozon_tokens.get_credentials(store_slug)
    lines: list[dict] = []
    warnings: list[str] = []
    for window_start, window_end in _windows(start, end, SOURCE_WINDOW_DAYS["OZON"]):
        since = datetime.combine(window_start, time.min, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        to = datetime.combine(window_end, time.min, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        for scheme, loader in (
            ("fbo", ozon_api.get_fbo_postings),
            ("fbs", ozon_api.get_fbs_postings_v4),
        ):
            try:
                postings = loader(client_id, api_key, since, to)
                lines.extend(_normalize_ozon(store_slug, postings, scheme))
            except Exception as exc:
                warnings.append(
                    f"{scheme.upper()} {window_start:%d.%m}-{window_end:%d.%m}: {type(exc).__name__}: {exc}"
                )
    if not lines and warnings:
        raise RuntimeError("; ".join(warnings[:3]))
    return lines, warnings


def _resolve_yandex_business_id(store_slug: str, api_key: str) -> int:
    business_id = yandex_tokens.get_business_id(store_slug)
    if business_id:
        return business_id
    campaigns = yandex_api.get_campaigns(api_key)
    ids = sorted(
        {
            _integer(campaign.get("business", {}).get("id") or campaign.get("businessId"))
            for campaign in campaigns
        }
        - {0}
    )
    if not ids:
        raise RuntimeError("Яндекс Маркет не вернул businessId кабинета")
    return ids[0]


def _sync_yandex(store_slug: str, start: date, end: date) -> tuple[list[dict], list[str]]:
    api_key = yandex_tokens.get_api_key(store_slug)
    business_id = _resolve_yandex_business_id(store_slug, api_key)
    orders: list[dict] = []
    warnings: list[str] = []
    for window_start, window_end in _windows(start, end, SOURCE_WINDOW_DAYS["YANDEX MARKET"]):
        try:
            orders.extend(
                yandex_api.get_business_orders(
                    api_key,
                    business_id,
                    window_start.isoformat(),
                    window_end.isoformat(),
                )
            )
        except Exception as exc:
            warnings.append(f"{window_start:%d.%m}-{window_end:%d.%m}: {type(exc).__name__}: {exc}")
    if not orders and warnings:
        raise RuntimeError("; ".join(warnings[:3]))
    return _normalize_yandex(store_slug, orders), warnings


def _sync_store_range(
    store_slug: str,
    marketplace: str,
    start: date,
    end: date,
    lookback_days: int,
    *,
    exact_order_period: bool,
) -> dict:
    marketplace = marketplace.upper()
    attempted_at = _now_iso()
    loader = {"WB": _sync_wb, "OZON": _sync_ozon, "YANDEX MARKET": _sync_yandex}[marketplace]
    try:
        lines, warnings = loader(store_slug, start, end)
        if exact_order_period:
            start_key = start.isoformat()
            end_key = end.isoformat()
            lines = [
                line
                for line in lines
                if start_key <= str(line.get("ordered_at") or "")[:10] < end_key
            ]
        rows = db.upsert_sales_order_lines(lines, attempted_at)
        ok = not warnings
        error = "; ".join(warnings)[:1500] or None
        db.record_sales_sync(store_slug, marketplace, ok, error, rows, lookback_days, attempted_at)
        db.record_sync_health(store_slug, marketplace, "orders", ok, error, attempted_at)
        return {"ok": ok, "rows": rows, "warnings": warnings}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:1500]
        db.record_sales_sync(store_slug, marketplace, False, error, 0, lookback_days, attempted_at)
        db.record_sync_health(store_slug, marketplace, "orders", False, error, attempted_at)
        logger.warning("Продажи %s/%s не обновлены: %s", marketplace, store_slug, error)
        return {"ok": False, "rows": 0, "error": error}


def sync_store(store_slug: str, marketplace: str, lookback_days: int = REFRESH_LOOKBACK_DAYS) -> dict:
    end = datetime.now(MOSCOW).date() + timedelta(days=1)
    start = end - timedelta(days=max(lookback_days, 1))
    return _sync_store_range(
        store_slug,
        marketplace,
        start,
        end,
        lookback_days,
        exact_order_period=False,
    )


def sync_store_period(store_slug: str, marketplace: str, start: date, end: date) -> dict:
    """Sync an exact half-open order period [start, end)."""

    if end <= start:
        raise ValueError("Дата окончания периода должна быть позже даты начала")
    return _sync_store_range(
        store_slug,
        marketplace,
        start,
        end,
        (end - start).days,
        exact_order_period=True,
    )


def _configured_stores(marketplace: str) -> list[str]:
    if marketplace == "WB":
        return [slug for slug in STORES if wb_tokens.has_token(slug)]
    if marketplace == "OZON":
        return [slug for slug in STORES if ozon_tokens.has_credentials(slug)]
    return [slug for slug in STORES if yandex_tokens.has_credentials(slug)]


def sync_all(refresh_days: int = REFRESH_LOOKBACK_DAYS) -> dict:

    report: dict[str, dict] = {}
    for marketplace in MARKETPLACES:
        platform_report: dict[str, dict] = {}
        for store_slug in _configured_stores(marketplace):
            days = refresh_days
            if not db.sales_has_history(store_slug, marketplace):
                days = max(days, INITIAL_LOOKBACK_DAYS[marketplace])
            platform_report[store_slug] = sync_store(store_slug, marketplace, days)
        report[marketplace] = platform_report
    return report


def parse_period(date_from: str, date_to: str, marketplace: str) -> tuple[date, date]:
    marketplace = marketplace.upper()
    if marketplace not in MARKETPLACES:
        raise ValueError("Неизвестный маркетплейс")
    try:
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
    except (TypeError, ValueError) as exc:
        raise ValueError("Укажите корректные даты периода") from exc
    if end < start:
        raise ValueError("Дата начала должна быть раньше даты окончания")
    days = (end - start).days + 1
    if days > MAX_RANGE_DAYS[marketplace]:
        raise ValueError(f"Для {marketplace} можно выбрать не больше {MAX_RANGE_DAYS[marketplace]} дней")
    today = datetime.now(MOSCOW).date()
    if end > today:
        raise ValueError("Дата окончания не может быть в будущем")
    oldest = today - timedelta(days=MAX_RANGE_DAYS[marketplace] - 1)
    if start < oldest:
        raise ValueError(f"Для {marketplace} доступны данные начиная с {oldest:%d.%m.%Y}")
    return start, end


def _daily_map(start: date, end: date, rows: list[dict]) -> list[dict]:
    by_day = {str(row.get("day")): row for row in rows}
    result = []
    cursor = start
    while cursor <= end:
        source = by_day.get(cursor.isoformat()) or {}
        result.append(
            {
                "date": cursor.isoformat(),
                "orders": round(_number(source.get("orders_amount")), 2),
                "fbo": round(_number(source.get("fbo_amount")), 2),
                "fbs": round(_number(source.get("fbs_amount")), 2),
                "cancellations": round(_number(source.get("cancellations_amount")), 2),
                "sales": round(_number(source.get("sales_amount")), 2),
                "orders_count": _integer(source.get("orders_count")),
                "fbo_count": _integer(source.get("fbo_count")),
                "fbs_count": _integer(source.get("fbs_count")),
                "cancellations_count": _integer(source.get("cancellations_count")),
                "sales_count": _integer(source.get("sales_count")),
            }
        )
        cursor += timedelta(days=1)
    return result


def _sum_series(series: list[dict], key: str) -> float:
    return round(sum(_number(item.get(key)) for item in series), 2)


def _delta(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


def dashboard(date_from: str, date_to: str, marketplace: str, store_slug: str | None = None) -> dict:
    start, end = parse_period(date_from, date_to, marketplace)
    marketplace = marketplace.upper()
    if store_slug and store_slug not in STORES:
        raise ValueError("Неизвестный магазин")
    exclusive_end = (end + timedelta(days=1)).isoformat()
    rows = db.get_sales_daily(start.isoformat(), exclusive_end, marketplace, store_slug)
    series = _daily_map(start, end, rows)

    period_days = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)
    previous_rows = db.get_sales_daily(
        previous_start.isoformat(),
        (previous_end + timedelta(days=1)).isoformat(),
        marketplace,
        store_slug,
    )
    previous = _daily_map(previous_start, previous_end, previous_rows)

    orders_amount = _sum_series(series, "orders")
    sales_amount = _sum_series(series, "sales")
    cancellations_amount = _sum_series(series, "cancellations")
    orders_count = sum(item["orders_count"] for item in series)
    cancellations_count = sum(item["cancellations_count"] for item in series)
    sales_count = sum(item["sales_count"] for item in series)
    states = db.get_sales_sync_states(marketplace, store_slug)
    last_success = max((row.get("last_success_at") or "" for row in states), default="") or None
    errors = [row for row in states if not row.get("ok")]

    return {
        "marketplace": marketplace,
        "store": store_slug or "all",
        "period": {"from": start.isoformat(), "to": end.isoformat(), "days": period_days},
        "limits": {
            "max_range_days": MAX_RANGE_DAYS[marketplace],
            "source_window_days": SOURCE_WINDOW_DAYS[marketplace],
        },
        "available": db.get_sales_available_range(marketplace, store_slug),
        "series": series,
        "totals": {
            "orders_amount": orders_amount,
            "fbo_amount": _sum_series(series, "fbo"),
            "fbs_amount": _sum_series(series, "fbs"),
            "sales_amount": sales_amount,
            "cancellations_amount": cancellations_amount,
            "orders_count": orders_count,
            "sales_count": sales_count,
            "cancellations_count": cancellations_count,
            "cancel_rate": round(cancellations_count / (orders_count + cancellations_count) * 100, 1)
            if orders_count + cancellations_count
            else 0.0,
            "sales_rate": round(sales_count / orders_count * 100, 1) if orders_count else 0.0,
            "orders_delta": _delta(orders_amount, _sum_series(previous, "orders")),
            "sales_delta": _delta(sales_amount, _sum_series(previous, "sales")),
        },
        "sync": {
            "last_success_at": last_success,
            "configured_stores": len(_configured_stores(marketplace)),
            "states_received": len(states),
            "errors": len(errors),
            "error_stores": [row.get("store_slug") for row in errors],
        },
    }


EXPORT_HEADERS = (
    ("store_slug", "Магазин"),
    ("marketplace", "Маркетплейс"),
    ("external_order_id", "Номер заказа"),
    ("ordered_at", "Дата заказа"),
    ("sold_at", "Дата продажи"),
    ("scheme", "Схема"),
    ("status", "Статус"),
    ("substatus", "Подстатус"),
    ("article", "Артикул"),
    ("barcode", "Баркод"),
    ("name", "Товар"),
    ("quantity", "Заказано, шт."),
    ("cancelled_quantity", "Отменено, шт."),
    ("sold_quantity", "Продано, шт."),
    ("order_amount", "Сумма заказа"),
    ("cancelled_amount", "Сумма отмены"),
    ("sale_amount", "Сумма продажи"),
    ("currency", "Валюта"),
)


def export_xlsx(date_from: str, date_to: str, marketplace: str, store_slug: str | None = None) -> bytes:
    start, end = parse_period(date_from, date_to, marketplace)
    if store_slug and store_slug not in STORES:
        raise ValueError("Неизвестный магазин")
    rows = db.get_sales_export_rows(
        start.isoformat(),
        (end + timedelta(days=1)).isoformat(),
        marketplace.upper(),
        store_slug,
    )
    return _xlsx_bytes(rows)


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _xlsx_cell(reference: str, value, style: int = 0) -> str:
    style_attr = f' s="{style}"' if style else ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}"{style_attr}><v>{value}</v></c>'
    text = escape(str(value or ""))
    return f'<c r="{reference}" t="inlineStr"{style_attr}><is><t>{text}</t></is></c>'


def _xlsx_bytes(rows: list[dict]) -> bytes:

    values = [[label for _, label in EXPORT_HEADERS]]
    values.extend([[row.get(key) for key, _ in EXPORT_HEADERS] for row in rows])
    widths = []
    for index, (_, label) in enumerate(EXPORT_HEADERS):
        longest = max([len(label)] + [len(str(row[index] or "")) for row in values[1:201]])
        widths.append(min(max(longest + 2, 11), 42))

    sheet_rows = []
    for row_index, row in enumerate(values, 1):
        cells = []
        for column_index, value in enumerate(row, 1):
            style = 1 if row_index == 1 else (2 if column_index in {15, 16, 17} else 0)
            cells.append(_xlsx_cell(f"{_column_name(column_index)}{row_index}", value, style))
        height = ' ht="24" customHeight="1"' if row_index == 1 else ""
        sheet_rows.append(f'<row r="{row_index}"{height}>{"".join(cells)}</row>')

    last_cell = f"{_column_name(len(EXPORT_HEADERS))}{max(len(values), 1)}"
    columns_xml = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(widths, 1)
    )
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
        'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f"<cols>{columns_xml}</cols><sheetData>{''.join(sheet_rows)}</sheetData>"
        f'<autoFilter ref="A1:{last_cell}"/></worksheet>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00 &quot;₽&quot;"/></numFmts>'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF243B72"/><bgColor indexed="64"/>'
        "</patternFill></fill></fills>"
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/>'
        "</cellStyles></styleSheet>"
    )

    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                "</Types>"
            ),
        )
        archive.writestr(
            "_rels/.rels",
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                "</Relationships>"
            ),
        )
        archive.writestr(
            "xl/workbook.xml",
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Заказы" sheetId="1" r:id="rId1"/></sheets></workbook>'
            ),
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                "</Relationships>"
            ),
        )
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    return output.getvalue()
