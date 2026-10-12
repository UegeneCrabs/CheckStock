"""Normalize Yandex Market orders, including subsidies and cancellations."""

from __future__ import annotations

from app.orders.normalizers.common import compact_json, integer, number, timestamp


def _money_value(value) -> float:
    return number(value)


def _yandex_item_amount(item: dict) -> float:
    prices = item.get("prices") or {}
    # API amounts already cover all units in the line. Include compensation
    # from Market in both orders and proportional cancellations/returns.
    if any(prices.get(key) is not None for key in ("payment", "cashback", "subsidy")):
        return sum(_money_value(prices.get(key)) for key in ("payment", "cashback", "subsidy"))
    quantity = max(integer(item.get("count"), 1), 1)
    return number(item.get("buyerPrice"), number(item.get("price"))) * quantity


def _yandex_status_counts(item: dict) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in item.get("itemStatuses") or []:
        if not isinstance(value, dict):
            continue
        status = str(value.get("status") or "").upper()
        result[status] = result.get(status, 0) + integer(value.get("count"))
    return result


def normalize(store_slug: str, orders: list[dict]) -> list[dict]:
    lines: list[dict] = []
    for order_index, order in enumerate(orders):
        order_key = str(order.get("orderId") or order.get("id") or f"ym-row-{order_index}")
        status = str(order.get("status") or "").upper()
        substatus = str(order.get("substatus") or "")
        program = str(order.get("programType") or "FBS").upper()
        ordered_at = timestamp(order.get("creationDate") or order.get("createdAt"))
        updated_at = timestamp(order.get("updateDate") or order.get("updatedAt") or ordered_at)
        delivery = order.get("delivery") or {}
        delivery_dates = delivery.get("dates") or {}
        delivered_at = delivery_dates.get("realDeliveryDate") or updated_at
        order_cancelled = status == "CANCELLED"
        order_delivered = status in {"DELIVERED", "DELIVERED_TO_BUYER", "COMPLETED"}
        for line_index, item in enumerate(order.get("items") or []):
            quantity = max(integer(item.get("count"), 1), 1)
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
                    "sold_at": timestamp(delivered_at) if sold_quantity else None,
                    "quantity": quantity,
                    "cancelled_quantity": cancelled_quantity,
                    "sold_quantity": sold_quantity,
                    "order_amount": amount,
                    "cancelled_amount": cancelled_amount,
                    "sale_amount": sale_amount,
                    "currency": "RUB",
                    "raw_json": compact_json(order),
                }
            )
    return lines
