"""Canonical returned goods from financial components (not logistics events).

Call per store/business. Inputs are authoritative, deduplicated report
snapshots. Never infer a payment date from statusChanged or bankOrderDate.
"""

from datetime import datetime
from collections import defaultdict
from math import isfinite
import hashlib
import json


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


def refund_revision(events):
    """Exact 52-bit numeric checksum, safe in the existing numeric fact store."""
    rows = sorted((item_key(e), e['refund_day'], e['count'], e['sumBillingPriceOfItems'],
                   e['refundBuyerPaymentAmount'], e.get('refund_basis', '')) for e in events)
    return int(hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:13], 16) if rows else 0


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
    """Match component quantity streams before selecting a reporting period.

    Cash and points describe the same units, not additional returned goods.
    For each unit cash takes precedence, otherwise use the earliest component.
    Ambiguous excess quantities fail closed instead of silently overcounting.
    Call with the complete authoritative history for this store/business.
    """
    orders = {item_key(row): row for row in order_rows}
    original_sources = {
        'Платёж покупателя': 'Возврат платежа покупателя',
        'Баллы за скидку Маркета': 'Возврат баллов за скидку Маркета',
        'Баллы за скидку Яндекс Плюс': 'Возврат баллов за скидку Яндекс Плюс',
        'Скидка за участие в совместных акциях': 'Возврат скидки за участие в совместных акциях',
    }
    unit_amounts = defaultdict(set)
    for row in payment_rows:
        source = original_sources.get(row.get('transactionSource'))
        amount, count = float(row.get('transactionSum') or 0), float(row.get('count') or 0)
        if source and amount > 0 and count > 0 and count.is_integer():
            unit_amounts[item_key(row), source].add(round(amount / count, 6))
    unique = {}
    for row in payment_rows:
        if not str(row.get("transactionSource") or "").startswith("Возврат "):
            continue
        if row.get("transactionType") not in {"Возврат", "Возврат списания"}:
            continue
        day = source_day(row.get("transactionDate"))
        if not day:
            continue
        amount = float(row.get("transactionSum") or 0)
        if not isfinite(amount):
            raise ValueError("Некорректная сумма финансового возврата")
        if amount >= 0:
            continue
        key = operation_key(row)
        if key in unique and float(unique[key].get("transactionSum") or 0) != amount:
            raise ValueError(f"Неоднозначная операция возврата денег: заказ {row.get('orderId')}")
        unique[key] = row

    components = defaultdict(lambda: defaultdict(list))
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
        if quantity <= 0 or not quantity.is_integer() or price is None or not isfinite(float(price)):
            raise ValueError(f"Нет количества или цены финансового возврата заказа {payment.get('orderId')}")
        item = item_key(order)
        delivered = float(order.get("deliveredOrReturned") or order.get("count") or 0)
        if delivered <= 0 or not delivered.is_integer():
            raise ValueError(f"Нет количества доставленного товара заказа {payment.get('orderId')}")
        # Some exports repeat the original order quantity on a PARTIAL refund.
        # Use the original payment component per unit when it proves an exact
        # integer quantity; never round a partial monetary compensation up.
        rates = unit_amounts[item, payment['transactionSource']]
        if len(rates) == 1:
            unit_amount = next(iter(rates))
            implied = abs(float(payment['transactionSum'])) / unit_amount
            units = round(implied)
            if units > 0 and abs(units * unit_amount - abs(float(payment['transactionSum']))) <= 0.02:
                quantity = units
            elif quantity > delivered:
                raise ValueError(f'Неясное количество частичного возврата заказа {item[0]}')
        components[item][payment['transactionSource']].append((key, {**payment, 'count': quantity}))

    events = []
    cash_source = "Возврат платежа покупателя"
    for item, sources in components.items():
        order = orders[item]
        delivered = int(float(order.get('deliveredOrReturned') or order.get('count')))
        price = float(order['billingPrice'] if order.get('billingPrice') is not None else order['partnerPriceForDelivery'])
        slots = defaultdict(list)
        for source, operations in sources.items():
            offset = 0
            for key, payment in sorted(operations, key=lambda pair: (
                source_day(pair[1]['transactionDate']), str(pair[1]['transactionDate'])[10:], repr(pair[0]))):
                quantity = int(float(payment['count']))
                if offset + quantity > delivered:
                    raise ValueError(f"Количество финансовых возвратов превышает количество товара заказа {item[0]}; требуется сверка операций")
                for unit in range(offset, offset + quantity):
                    slots[unit].append((source != cash_source, source_day(payment['transactionDate']), key, payment))
                offset += quantity
        # Aggregate units back to operations, preserving partial quantities.
        selected = {}
        for candidates in slots.values():
            _, day, key, payment = min(candidates, key=lambda c: (c[0], c[1], repr(c[2])))
            if not date_from <= day <= date_to:
                continue
            if key not in selected:
                selected[key] = [day, payment, 0]
            selected[key][2] += 1
        for key, (day, payment, quantity) in selected.items():
            confirmed = payment['transactionSource'] == cash_source
            if confirmed:
                buyer_amount = abs(float(payment['transactionSum'])) * quantity / float(payment['count'])
            else:
                original = order.get('buyerPaymentAmount')
                rates = unit_amounts[item, cash_source]
                if len(rates) == 1:
                    buyer_amount = next(iter(rates)) * quantity
                elif original is not None and isfinite(float(original)) and float(original) >= 0:
                    buyer_amount = float(original) * quantity / delivered
                else:
                    raise ValueError(f"Нет суммы оплаты покупателя для возврата заказа {item[0]}")
            events.append({
                **order, 'refund_event_key': repr(key), 'refund_day': day,
                'refund_basis': payment['transactionSource'], 'refund_money_confirmed': confirmed,
                'count': quantity, 'quantity': quantity,
                'sumBillingPriceOfItems': round(price * quantity, 2),
                'refundBuyerPaymentAmount': -round(buyer_amount, 2),
            })
    return events
