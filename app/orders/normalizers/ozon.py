"""Normalize Ozon FBO and FBS posting responses."""

from __future__ import annotations

from app.orders.normalizers.common import compact_json, integer, number, timestamp


def _ozon_financial_products(posting: dict) -> dict[str, dict]:
    financial = posting.get("financial_data") or {}
    rows = financial.get("products") or posting.get("financial_products") or []
    result = {}
    for row in rows:
        key = str(row.get("product_id") or row.get("sku") or row.get("offer_id") or "")
        if key:
            result[key] = row
    return result


def normalize(store_slug: str, postings: list[dict], scheme: str) -> list[dict]:
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
        ordered_at = timestamp(
            posting.get("created_at") or posting.get("in_process_at") or posting.get("shipment_date")
        )
        updated_at = timestamp(posting.get("updated_at") or posting.get("status_updated_at") or ordered_at)
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
            quantity = max(integer(product.get("quantity"), 1), 1)
            unit_price = number(
                product.get("price"),
                number(financial.get("price"), number(financial.get("old_price"))),
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
                    "sold_at": timestamp(sold_source) if delivered else None,
                    "quantity": quantity,
                    "cancelled_quantity": quantity if cancelled else 0,
                    "sold_quantity": quantity if delivered and not cancelled else 0,
                    "order_amount": amount,
                    "cancelled_amount": amount if cancelled else 0.0,
                    "sale_amount": amount if delivered and not cancelled else 0.0,
                    "currency": str(product.get("currency_code") or "RUB"),
                    "raw_json": compact_json(posting),
                }
            )
    return lines
