"""Normalize WB statistics and FBS order responses."""

from __future__ import annotations

from app.orders.normalizers.common import compact_json, integer, number, timestamp

WB_FBS_CANCELLED_SUPPLIER_STATUSES = {"cancel"}
WB_FBS_CANCELLED_STATUSES = {
    "canceled",
    "canceled_by_client",
    "canceled_by_missed_call",
    "declined_by_client",
    "defect",
}
WB_FBS_SOLD_STATUSES = {"sold"}


def _wb_sale_totals(rows: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for row in rows:
        srid = str(row.get("srid") or "").strip()
        if not srid:
            continue
        amount = number(
            row.get("finishedPrice"),
            number(row.get("priceWithDisc"), number(row.get("totalPrice"))),
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
            sold_at = timestamp(row.get("date") or row.get("lastChangeDate"))
            if not current["sold_at"] or sold_at < current["sold_at"]:
                current["sold_at"] = sold_at
        else:
            returned_at = timestamp(row.get("date") or row.get("lastChangeDate"))
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


def normalize(
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
        amount = number(
            row.get("finishedPrice"),
            number(row.get("priceWithDisc"), number(row.get("totalPrice"))),
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
                "ordered_at": timestamp(row.get("date") or row.get("lastChangeDate")),
                "source_updated_at": timestamp(row.get("lastChangeDate") or row.get("date")),
                "cancelled_at": timestamp(row.get("cancelDate")) if cancelled else None,
                "sold_at": sale.get("sold_at") or None,
                "returned_at": sale.get("returned_at") or None,
                "quantity": 1,
                "cancelled_quantity": 1 if cancelled else 0,
                "sold_quantity": min(integer(sale.get("quantity")), 1),
                "return_quantity": integer(sale.get("return_quantity")),
                "order_amount": round(amount, 2),
                "cancelled_amount": round(amount, 2) if cancelled else 0.0,
                "sale_amount": round(number(sale.get("amount")), 2),
                "return_amount": round(number(sale.get("return_amount")), 2),
                "currency": "RUB",
                "raw_json": compact_json(row),
            }
        )
    return lines


def _wb_fbs_amount(order: dict) -> float:
    """Return the buyer price in the seller's currency from WB's amount x100 fields."""

    for field in ("convertedFinalPrice", "convertedPrice", "finalPrice", "price"):
        value = order.get(field)
        if value is not None:
            return round(number(value) / 100, 2)
    return 0.0


def normalize_fbs(
    store_slug: str,
    orders: list[dict],
    statuses: dict[int, dict],
) -> list[dict]:
    lines: list[dict] = []
    for index, order in enumerate(orders):
        order_id = integer(order.get("id"))
        current_status = statuses.get(order_id) or {}
        supplier_status = str(current_status.get("supplierStatus") or "").strip().casefold()
        wb_status = str(current_status.get("wbStatus") or "").strip().casefold()
        cancelled = (
            supplier_status in WB_FBS_CANCELLED_SUPPLIER_STATUSES or wb_status in WB_FBS_CANCELLED_STATUSES
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
        ordered_at = timestamp(order.get("createdAt"))
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
                "raw_json": compact_json(raw),
            }
        )
    return lines


def merge_lines(statistics_lines: list[dict], fbs_lines: list[dict]) -> list[dict]:
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
            integer(existing.get("cancelled_quantity")),
            integer(fbs_line.get("cancelled_quantity")),
        )
        existing["sold_quantity"] = max(
            integer(existing.get("sold_quantity")),
            integer(fbs_line.get("sold_quantity")),
        )
        existing["cancelled_amount"] = max(
            number(existing.get("cancelled_amount")),
            number(fbs_line.get("cancelled_amount")),
        )
        existing["sale_amount"] = max(
            number(existing.get("sale_amount")),
            number(fbs_line.get("sale_amount")),
        )
    return merged
