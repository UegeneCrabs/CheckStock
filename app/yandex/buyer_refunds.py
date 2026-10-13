"""Pure, shared recognition of buyer-money refunds (not logistics events).

Call per store/business. Inputs are authoritative, deduplicated report
snapshots. Never infer a payment date from statusChanged or bankOrderDate.
"""

from datetime import datetime


RETURN_STATUSES = {
    "Возврат оформлен", "Возврат отправлен", "Возврат готов к передаче вам",
    "Возврат передан вам", "Возврат принят", "Возврат принят на складе",
    "Полный возврат принят на складе",
}


def source_day(value):
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            pass
    return None


def item_key(row):
    return (str(row.get("orderId") or ""), str(row.get("shopSku") or ""))


def operation_key(row):
    """Report metadata and amount corrections must not create new events.

    Market frequently omits transactionId. In that case the finest available
    timestamp, order, SKU, source and quantity identify the operation. If the
    same export contains conflicting amounts for that key, reject it.
    """
    identifier = row.get("transactionId")
    return (
        str(row.get("partnerId") or ""), *item_key(row),
        str(row.get("transactionSource") or ""),
        str(identifier) if identifier not in (None, "") else
        (str(row.get("transactionDate") or ""), str(row.get("count") or "")),
    )


def is_returned_sale(row):
    status = str(row.get("offerStatus") or "").strip()
    if status in RETURN_STATUSES:
        return True
    # A refund on a never-delivered cancellation/non-purchase is not COGS
    # or a reversal of sales. A delivery date is required for this fallback.
    return status in {"Отменён", "Невыкуп отправлен", "Невыкуп принят на складе",
                      "Невыкуп готов к передаче вам", "Доставлен покупателю"} and bool(
        source_day(row.get("deliveryDate"))
    )


def build_refunds(order_rows, payment_rows, date_from, date_to):
    """One event per buyer-payment operation; partial quantities stay separate.

    Zero/missing quantities and ambiguous amounts are errors, not invented
    single-unit returns. Points and other refund components never add goods.
    """
    orders = {item_key(row): row for row in order_rows}
    unique = {}
    for row in payment_rows:
        if row.get("transactionSource") != "Возврат платежа покупателя":
            continue
        if row.get("transactionType") != "Возврат":
            continue
        day = source_day(row.get("transactionDate"))
        if not day or not date_from <= day <= date_to:
            continue
        amount = float(row.get("transactionSum") or 0)
        if amount >= 0:
            continue
        key = operation_key(row)
        if key in unique and float(unique[key].get("transactionSum") or 0) != amount:
            raise ValueError(f"Неоднозначная операция возврата денег: заказ {row.get('orderId')}")
        unique[key] = row

    events = []
    quantities = {}
    for key, payment in unique.items():
        order = orders.get(item_key(payment))
        if order is None:
            raise ValueError(f"Нет товара заказа {payment.get('orderId')} для проверки финансового возврата")
        if not is_returned_sale(order):
            continue
        quantity = float(payment.get("count") or 0)
        price = order.get("billingPrice")
        if price is None:
            price = order.get("partnerPriceForDelivery")
        if quantity <= 0 or not quantity.is_integer() or price is None:
            raise ValueError(f"Нет количества или цены финансового возврата заказа {payment.get('orderId')}")
        item = item_key(order)
        quantities[item] = quantities.get(item, 0) + quantity
        delivered = float(order.get("deliveredOrReturned") or order.get("count") or 0)
        if delivered > 0 and quantities[item] > delivered:
            raise ValueError(f"Количество финансовых возвратов превышает количество товара заказа {payment.get('orderId')}; требуется сверка операций")
        events.append({
            **order, "refund_event_key": repr(key),
            "refund_day": source_day(payment["transactionDate"]),
            "count": int(quantity), "quantity": int(quantity),
            "sumBillingPriceOfItems": round(float(price) * quantity, 2),
            "refundBuyerPaymentAmount": float(payment["transactionSum"]),
        })
    return events
