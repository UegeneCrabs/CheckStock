"""Versioned field mapping; column names are verified against Partner API docs.

Rows in monthly realization/services files are snapshots, not global event ids.
The row ordinal preserves multiplicity. Replacing a month removes its old revision.
"""

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.core.domain import MOSCOW_TIMEZONE
from app.dto.finance import FinanceEvent, SourceBatch


class FinanceSourceError(ValueError):
    def __init__(self, message):
        self.public_message = message
        super().__init__(message)


def number(value):
    if value in (None, "", "—", "-"):
        return None
    try:
        result = Decimal(str(value).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except InvalidOperation as error:
        raise FinanceSourceError("В источнике некорректное денежное поле") from error
    if not result.is_finite():
        raise FinanceSourceError("В источнике неконечное денежное поле")
    return result


def count(value):
    result = number(value)
    if result is None or result < 1 or result != int(result):
        raise FinanceSourceError("В источнике нет достоверного количества")
    return int(result)


def event_day(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for pattern in ("%d.%m.%Y", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
            try:
                parsed = datetime.strptime(raw, pattern)
                break
            except ValueError:
                continue
        if parsed is None:
            raise FinanceSourceError("В источнике отсутствует однозначная дата события") from None
    return parsed.astimezone(MOSCOW_TIMEZONE).date() if parsed.tzinfo else parsed.date()


def rows(sheets, sheet):
    if sheet not in sheets:
        raise FinanceSourceError(f"В архиве отсутствует обязательный лист {sheet}")
    value = sheets[sheet]
    if isinstance(value, dict) and set(value) == {sheet}:
        value = value[sheet]
    if isinstance(value, dict) and set(value) == {"rows"}:
        value = value["rows"]
    if not isinstance(value, list) or any(not isinstance(r, dict) for r in value):
        raise FinanceSourceError(f"Неизвестная структура листа {sheet}")
    return value


def campaign(row, connection):
    if row.get("businessId") is not None and int(row["businessId"]) != connection["business_id"]:
        raise FinanceSourceError("Архив другого кабинета")
    value = row.get("partnerId")
    if value in (None, "", "—", "-"):
        return None
    try:
        value = int(value)
        if value <= 0:
            return None
    except (ValueError, TypeError):
        raise FinanceSourceError("Неизвестный формат Campaign ID") from None
    return value


# (category, amount column, event date column). Percent and tariff columns are never summed.
SERVICE_FIELDS = {
    "placement": ("commission", "totalAmount", "serviceDateTime"),
    "sale_commission": ("commission", "totalAmount", "serviceDateTime"),
    "item_booking": ("other", "totalAmount", "serviceDateTime"),
    "expropriation": ("commission", "fullPrice", "dateTime"),
    **{
        name: ("logistics", "servicePrice", "serviceDateTime")
        for name in (
            "warehouse_processing",
            "goods_acceptance",
            "delivery",
            "crossregional_delivery",
            "express_delivery",
            "delivery_from_abroad",
            "agency_commission_3pl",
            "reception_of_surplus",
            "export_from_warehouse",
            "intake_logistics",
        )
    },
    **{
        name: ("logistics", "servicePrice", "serviceDate")
        for name in ("delivery_via_transit_warehouse", "order_processing", "order_processing_on_warehouse")
    },
    **{
        name: ("acquiring", "servicePrice", "serviceDateTime")
        for name in ("payment_accepting", "payment_transfer", "money_withdraw", "installment_plan")
    },
    "loyalty_and_reviews": ("advertising", "servicePrice", "serviceDateTime"),
    **{
        name: ("advertising", "servicePrice", "serviceDate")
        for name in (
            "boost",
            "shelf",
            "cpm-boost",
            "product-banners",
            "banners",
            "pushes",
            "popups",
            "mailing",
            "web",
            "tv",
            "yandex-market",
            "pads",
        )
    },
    **{
        name: ("storage", "paidStorage", "serviceDate")
        for name in ("paid_storage_after_01-06-22", "paid_storage_after_01-09-26")
    },
    **{
        name: ("storage", "servicePrice", "serviceDate")
        for name in ("paid_storage_before_31-05-22", "storage_of_returns")
    },
    **{name: ("other", "servicePrice", "serviceDateTime") for name in ("product_marking", "utilization")},
    **{
        name: ("other", "servicePrice", "serviceDate")
        for name in ("extended_service_access", "business_subscription", "personal_manager")
    },
}


def realization(connection, raw, start, end):
    operational = defaultdict(set)
    for row in rows(raw["operational"], "orders_and_offers_transactions"):
        cid = campaign(row, connection)
        if cid not in connection["campaign_ids"]:
            continue
        price = number(row.get("billingPrice"))
        if price is not None and price < 0:
            raise FinanceSourceError("Отрицательная цена покупателя в операционном отчёте")
        # Join the order + SKU, independent of payment date and item status order.
        if price is not None:
            operational[(cid, str(row.get("orderId", "")), str(row.get("shopSku", "")))].add(price)
    events, issues = [], []
    for cid in connection["campaign_ids"]:
        sheets = raw["campaigns"].get(str(cid))
        if not isinstance(sheets, dict):
            raise FinanceSourceError("Нет полного отчёта реализации выбранной кампании")
        for sheet in sheets:
            if sheet not in {"delivered", "returned", "transferred_to_delivery", "unredeemed"} and rows(
                sheets, sheet
            ):
                issues.append(f"Неизвестный лист реализации {sheet}")
        for sheet, kind, qty, date_field, total in (
            ("delivered", "sale", "deliveredCount", "deliveryDate", "deliveredPriceSumWithVatAndDiscounts"),
            (
                "returned",
                "return",
                "returnedCount",
                "returnWarehouseOrScAcceptDate",
                "returnPriceSumWithVatAndDiscounts",
            ),
        ):
            for index, row in enumerate(rows(sheets, sheet)):
                day = event_day(row.get(date_field))
                if day.replace(day=1) != start:
                    raise FinanceSourceError("Дата реализации вне заказанного месяца")
                # This endpoint accepts a whole month, even when the requested
                # coverage stops at yesterday. Today's facts remain unpublished.
                if day > end:
                    continue
                quantity = count(row.get(qty))
                order, article = str(row.get("orderId") or ""), str(row.get("yourSku") or "")
                prices = operational[(cid, order, article)]
                buyer = next(iter(prices)) * quantity if len(prices) == 1 else None
                seller = number(row.get(total))
                if seller is not None and seller < 0:
                    raise FinanceSourceError("Неизвестный знак стоимости реализации; проверьте маппинг")
                events.append(
                    FinanceEvent(
                        key=f"{cid}:{sheet}:{index}",
                        source="realization",
                        source_sheet=sheet,
                        day=day,
                        kind=kind,
                        campaign_id=cid,
                        order_id=order,
                        article=article,
                        quantity=quantity,
                        seller=seller,
                        buyer=buyer,
                        original_day=event_day(row["deliveryDate"])
                        if kind == "return" and row.get("deliveryDate")
                        else None,
                        issues=()
                        if buyer is not None
                        else ("Нет однозначной цены покупателя для заказа и SKU",),
                    )
                )
    return SourceBatch(
        source="realization", start=start, end=end, events=tuple(events), raw=raw, issues=tuple(issues)
    )


def services(connection, raw, start, end):
    if not raw:
        raise FinanceSourceError("Пустой архив услуг не подтверждает отсутствие начислений")
    events, issues, unallocated = [], [], []
    for sheet in sorted(raw):
        mapping = SERVICE_FIELDS.get(sheet)
        for index, row in enumerate(rows(raw, sheet)):
            cid = campaign(row, connection)
            if cid is not None and cid not in connection["campaign_ids"]:
                continue
            if cid is None:
                # Keep raw once in this account's report. Never expose its money to a partial account scope.
                issues.append(
                    "Есть нераспределённые расходы кабинета; требуется подтверждённое правило распределения"
                )
                if mapping:
                    category, amount_field, date_field = mapping
                    unallocated.append(
                        FinanceEvent(
                            key=f"{sheet}:{index}",
                            day=event_day(row.get(date_field)),
                            kind="expense",
                            source="services",
                            source_sheet=sheet,
                            category=category,
                            amount=number(row.get(amount_field)),
                        )
                    )
                continue
            if mapping is None:
                issues.append(f"Не классифицирован лист услуг {sheet}")
                events.append(
                    FinanceEvent(
                        key=f"{sheet}:{index}",
                        day=start,
                        kind="unknown",
                        source="services",
                        source_sheet=sheet,
                        campaign_id=cid,
                        category="unclassified",
                        issues=("Неизвестная схема: дата и сумма не определены",),
                    )
                )
                continue
            category, amount_field, date_field = mapping
            day = event_day(row.get(date_field))
            if not start <= day <= end:
                raise FinanceSourceError("Дата услуги вне запрошенного периода")
            amount = number(row.get(amount_field))
            # A reversal must already carry a monetary sign. Do not infer it from translated labels.
            record = str(row.get("recordType") or "").lower()
            if (
                any(token in record for token in ("сторно", "reversal", "возврат"))
                and amount is not None
                and amount > 0
            ):
                amount = None
                issues.append(f"Лист {sheet}: знак сторно не подтверждён")
            events.append(
                FinanceEvent(
                    key=f"{sheet}:{index}",
                    day=day,
                    kind="expense",
                    source="services",
                    source_sheet=sheet,
                    campaign_id=cid,
                    category=category,
                    amount=amount,
                    article=str(row.get("shopSku") or row.get("sku") or ""),
                    order_id=str(row.get("orderId") or row.get("orderNumber") or ""),
                )
            )
    return SourceBatch(
        source="services",
        start=start,
        end=end,
        raw=raw,
        events=tuple(events),
        unallocated=tuple(unallocated),
        issues=tuple(dict.fromkeys(issues)),
    )


def orders(connection, raw, start, end):
    events = []
    for row in rows(raw, "orders"):
        cid = int(row["campaignId"])
        if cid not in connection["campaign_ids"]:
            continue
        day = event_day(row.get("creationDate"))
        if not start <= day <= end:
            continue
        items = row.get("items")
        if not isinstance(items, list) or not items:
            raise FinanceSourceError("Заказ без состава товаров")
        for item in items:
            quantity = count(item.get("count"))
            prices = item.get("prices") or {}
            # BusinessOrderItemDTO.prices already covers all units, not a unit tariff.
            total = prices
            amounts = []
            for name in ("payment", "subsidy", "cashback"):
                component = total.get(name)
                if component is not None:
                    if component.get("currencyId") not in ("RUR", "RUB"):
                        raise FinanceSourceError("Поддерживаются только рублёвые заказы")
                    amounts.append(number(component.get("value")))
            amount = sum(amounts, Decimal(0)) if amounts and all(a is not None for a in amounts) else None
            identifier = item.get("id")
            if identifier is None or not row.get("orderId"):
                raise FinanceSourceError("Заказ или строка без идентификатора")
            events.append(
                FinanceEvent(
                    key=f"{cid}:{row['orderId']}:{identifier}",
                    day=day,
                    kind="order",
                    source="orders",
                    source_sheet="BusinessOrders",
                    campaign_id=cid,
                    order_id=str(row["orderId"]),
                    line_id=str(identifier),
                    article=str(item.get("offerId") or ""),
                    quantity=quantity,
                    amount=amount,
                )
            )
    return SourceBatch(source="orders", start=start, end=end, events=tuple(events), raw=raw)


def payments(connection, raw, start, end):
    events, issues, income_issues, seen = [], [], [], {}
    unknown_income = {}
    # These settlements are already included in realization; count no additional
    # income from them. Any other source needs an explicitly verified mapping.
    settled_sources = {
        "Платеж покупателя",
        "Компенсация за скидку",
        "Компенсация за оплату бонусами Спасибо",
        "Компенсация за оплату бонусами Яндекс Плюса",
        "Возврат платежа покупателя",
        "Возврат компенсации за скидку",
        "Возврат компенсации за оплату бонусами Спасибо",
        "Возврат компенсации за оплату бонусами Яндекс Плюса",
    }
    for row in rows(raw, "transaction_date"):
        cid = campaign(row, connection)
        if cid is not None and cid not in connection["campaign_ids"]:
            continue
        if cid is None:
            issues.append("Не распределена выплата кабинета")
            income_issues.append("Есть нераспределённые взаиморасчёты кабинета")
            continue
        if row.get("transactionSource") not in settled_sources:
            income_issues.append(
                "Не классифицирован дополнительный источник взаиморасчётов; прочие начисления и прибыль неполны"
            )
            income_day = event_day(row.get("transactionDate"))
            if start <= income_day <= end:
                income_key = f"income:{cid}:{row.get('transactionId') or ''}"
                unknown_income[income_key] = FinanceEvent(
                    key=income_key,
                    day=income_day,
                    kind="income",
                    source="payments",
                    source_sheet="transaction_date",
                    campaign_id=cid,
                    order_id=str(row.get("orderId") or ""),
                    article=str(row.get("shopSku") or ""),
                    amount=None,
                    issues=("Не классифицировано: " + str(row.get("transactionSource") or "нет источника"),),
                )
        # A transaction is not a bank payout. Store the signed allocated transaction
        # amount on its bank-order date only when the report confirms that it is paid.
        status = str(row.get("paymentStatus") or "").strip().lower()
        if status in {"не выплачен", "не выплачено", "not paid", "unpaid"}:
            continue
        if status not in {"выплачен", "выплачено", "paid"}:
            issues.append("Неизвестный статус выплаты; требуется сверка отчёта")
            continue
        day = event_day(row.get("bankOrderDate"))
        if not start <= day <= end:
            continue
        tid = str(row.get("transactionId") or "")
        if not tid:
            raise FinanceSourceError("Платёж без ID транзакции")
        key = f"{cid}:{tid}"
        signature = (day, number(row.get("transactionSum")), str(row.get("bankOrderId") or ""))
        if key in seen:
            if seen[key] != signature:
                raise FinanceSourceError("Конфликт версий платёжной транзакции")
            continue
        seen[key] = signature
        amount = signature[1]
        transaction_type = row.get("transactionType")
        if transaction_type not in {"Начисление", "Возврат", "Удержание"}:
            amount = None
            issues.append("Неизвестный тип платёжной транзакции; знак суммы не подтверждён")
        elif transaction_type in {"Возврат", "Удержание"} and amount is not None and amount > 0:
            amount = None
            issues.append("Положительная сумма возврата или удержания требует сверки знака")
        events.append(
            FinanceEvent(
                key=key,
                day=day,
                kind="payment",
                source="payments",
                source_sheet="transaction_date",
                campaign_id=cid,
                order_id=str(row.get("orderId") or ""),
                article=str(row.get("shopSku") or ""),
                amount=amount,
                line_id=signature[2],
            )
        )
    return SourceBatch(
        source="payments",
        start=start,
        end=end,
        events=tuple([*events, *unknown_income.values()]),
        raw=raw,
        issues=tuple(dict.fromkeys(issues)),
        income_issues=tuple(dict.fromkeys(income_issues)),
    )


PARSERS = {"realization": realization, "services": services, "orders": orders, "payments": payments}
