"""Import the Yandex unified orders report into the local financial ledger."""

import hashlib
import json
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC, date, datetime, timedelta
from io import BytesIO

from app.repositories import yandex_financial, yandex_source_values
from app.stock import sales as sales_sync
from app.yandex import api, tokens

REPORT_KIND = "united-orders"
SERVICES_REPORT_KIND = "united-services"
NETTING_REPORT_KIND = "united-netting"
# A completed return can arrive well after delivery. Rebuild the preceding
# financial window each day so its confirmation is reflected in the report.
FINANCIAL_REPORT_LOOKBACK_DAYS = 90

def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


_REPORTABLE_RETURN_OFFER_STATUSES = {
    "Возврат оформлен",
    "Возврат отправлен",
    "Возврат готов к передаче вам",
    "Возврат передан вам",
    "Возврат принят",
    "Возврат принят на складе",
    "Полный возврат принят на складе",
}
_FINANCIALLY_REVERSED_OFFER_STATUSES = {
    "Отменён",
    "Невыкуп отправлен",
    "Невыкуп принят на складе",
    "Невыкуп готов к передаче вам",
}
_REALISATION_RETURN_STATUS_PREFIX = "Полный возврат"


def _is_financial_return(row: dict) -> bool:
    """Whether an order row reverses an already accounted buyout.

    Regular cancellation and unredeemed statuses do not affect a buyout: the
    item may never have been delivered or paid for. They become a reversal
    only where the same transaction has a delivery date and Market provides a
    non-zero refund to the buyer. Explicit return statuses are retained even
    while the refund amount is still absent from a report row.
    """

    status = str(row.get("offerStatus") or "").strip()
    # PiData's «Возвращено» is posted when a redeemed product enters the
    # return lifecycle.  A later acceptance or handover is the same return,
    # and is deduplicated by order and SKU before a daily report is built.
    if status in _REPORTABLE_RETURN_OFFER_STATUSES:
        return True
    return (
        status in _FINANCIALLY_REVERSED_OFFER_STATUSES
        and _source_day(row.get("deliveryDate")) is not None
        and _number(row.get("refundBuyerPaymentAmount")) != 0
    )


def _is_unredeemed_order(row: dict) -> bool:
    """Whether an operation belongs to a cancelled or unredeemed order.

    Marketplace can include a technical payment-transfer operation for a
    non-redemption. It must not increase turnover of redeemed goods: unlike a
    financial return, the item was never delivered to the buyer.
    """

    return (
        str(row.get("offerStatus") or "").strip() in _FINANCIALLY_REVERSED_OFFER_STATUSES
        and _source_day(row.get("deliveryDate")) is None
    )


def _realisation_returns(order_sheets: dict[str, list[dict]]) -> list[dict]:
    """Return completed returns from the Marketplace realisation report.

    The transactions sheet describes the current order state and is useful as
    a fallback for daily snapshots. The realisation sheet is authoritative for
    a selected period: it contains only items that were first delivered and
    then accepted back by Market.
    """

    return [
        row
        for row in order_sheets.get("services_and_orders_margin", [])
        if str(row.get("orderStatus") or "").strip().startswith(_REALISATION_RETURN_STATUS_PREFIX)
    ]


def _cost_for_items(items: list[dict], purchase_prices: dict[str, float]) -> float | None:
    """Return cost price for item quantities, or ``None`` when a price is absent.

    The same item quantities are used by the buyout counter: delivered order
    lines add to the result and a completed financial return subtracts from
    it. Keeping this helper quantity-based prevents the cost price from
    drifting from the displayed buyout count.
    """

    total = 0.0
    for item in items:
        article = str(item.get("article") or item.get("shopSku") or "").strip()
        price = purchase_prices.get(article)
        if price is None:
            return None
        total += price * _number(item.get("quantity") or item.get("count") or 1)
    return round(total, 2)


def _redeemed_payment_transfer_rows(
    service_sheets: dict[str, list[dict]], order_sheets: dict[str, list[dict]]
) -> list[dict]:
    """Return exactly the payment rows that define buyout turnover."""

    orders_by_id = {
        row.get("orderId"): row
        for row in order_sheets.get(
            "payment_transfer_order_statuses",
            order_sheets.get("orders_and_offers_transactions", []),
        )
        if row.get("orderId") is not None
    }
    return [
        row
        for row in service_sheets.get("payment_transfer", [])
        if not _is_unredeemed_order(orders_by_id.get(row.get("orderId"), {}))
    ]


def _buyout_return_rows(order_sheets: dict[str, list[dict]]) -> list[dict]:
    """Return exactly the financial reversals subtracted from buyout turnover."""

    return _realisation_returns(order_sheets) or order_sheets.get("returns", [])


def _buyout_basket(
    service_sheets: dict[str, list[dict]], order_sheets: dict[str, list[dict]]
) -> tuple[list[dict], list[dict]]:
    """Build the one line-item basket used by every redeemed-goods metric.

    The official unified orders archive has buyer price on the transaction
    line, while the services archive has seller turnover on the payment
    transfer line.  Joining them by ``orderId + shopSku`` gives one canonical
    redeemed item.  Count, both turnover columns and COGS must all use this
    same basket; joining buyer price only by order ID loses multi-SKU orders.
    """

    order_rows: dict[tuple[object, str], dict] = {}
    order_rows_by_id: dict[object, list[dict]] = {}
    for row in order_sheets.get("orders_and_offers_transactions", []):
        order_id = row.get("orderId")
        sku = str(row.get("shopSku") or row.get("offerId") or "").strip()
        if order_id is not None and sku:
            order_rows[(order_id, sku)] = row
        if order_id is not None:
            order_rows_by_id.setdefault(order_id, []).append(row)

    redeemed: list[dict] = []
    for payment in _redeemed_payment_transfer_rows(service_sheets, order_sheets):
        order_id = payment.get("orderId")
        sku = str(payment.get("shopSku") or payment.get("offerId") or "").strip()
        item = dict(payment)
        item["quantity"] = _number(payment.get("count") or payment.get("quantity") or 1)
        order_row = order_rows.get((order_id, sku))
        # Old archives can lack SKU.  Retain a safe compatibility fallback
        # only where the order has exactly one item; multi-item orders are
        # never guessed or allocated across lines.
        if order_row is None and not sku and len(order_rows_by_id.get(order_id, [])) == 1:
            order_row = order_rows_by_id[order_id][0]
        if order_row is not None:
            item["buyerPaymentAmount"] = order_row.get("buyerPaymentAmount")
        redeemed.append(item)

    return redeemed, _buyout_return_rows(order_sheets)


def _pnl_values(
    service_sheets: dict[str, list[dict]],
    order_sheets: dict[str, list[dict]],
    *,
    campaign_count: int = 1,
) -> dict[str, float | None]:
    """Map Yandex accruals to the PIData-compatible payment calculation.

    ``К перечислению на счет`` is a calculated amount, not a bank payment:
    price of redeemed goods minus the Market commission and every accompanying
    charge of the same period.  Actual payment orders remain in the separate
    payout ledger because Market can adjust them later.
    """

    def total(sheet: str, field: str = "servicePrice") -> float:
        return round(sum(abs(_number(row.get(field))) for row in service_sheets.get(sheet, [])), 2)

    def signed_total(sheet: str, field: str = "servicePrice") -> float:
        """Keep service reversals negative instead of turning them into costs."""

        return round(sum(_number(row.get(field)) for row in service_sheets.get(sheet, [])), 2)

    payment_transfer_rows, return_rows = _buyout_basket(service_sheets, order_sheets)
    transferred_order_ids = {
        row.get("orderId") for row in payment_transfer_rows if row.get("orderId") is not None
    }

    # ``amountWithoutBonuses`` is the Marketplace commission before it is
    # offset with Market bonuses.  PIData treats it as the marketplace fee.
    # A placement operation without a matching buyer-payment operation is not
    # a redeemed good of this period and must not inflate the commission.
    commission = round(
        sum(
            _number(row.get("amountWithoutBonuses"))
            for row in service_sheets.get("placement", [])
            if row.get("orderId") in transferred_order_ids
        ),
        2,
    )
    # Эквайринг относится к оплате покупателем, а не к факту выкупа. Поэтому
    # берём все операции перечисления платежа: PiData также включает в эту
    # статью техническую операцию, если заказ позднее стал невыкупом.
    payment_transfer = total("payment_transfer")
    # PiData includes both legs of a transit delivery (including acceptance
    # at the transit warehouse) in logistics, not in "Other".
    logistics = round(
        signed_total("delivery")
        + signed_total("crossregional_delivery")
        + signed_total("delivery_via_transit_warehouse"),
        2,
    )
    paid_acceptance = total("payment_accepting")
    # В P&L PiData строка «Другие → полученные с МП» соответствует
    # вознаграждению/корректировке за просроченное исполнение заказа из
    # placement-отчёта. Это отдельная от эквайринга статья, но в компактной
    # таблице мы показываем их одной строкой «Иное».
    marketplace_other = total("placement", "lateOrderExecutionFeeTariff")
    # The payment fields, not technical ``servicePrice``, are the actual
    # marketing withholding. PiData's «Маркетинг» includes every one of these
    # services: boost sales, boost with payment for impressions and product
    # banners.
    advertising = round(
        total("boost", "postpaid")
        + total("cpm-boost", "payment")
        # Banner rows have no ``payment`` value for some dates. They are a
        # business-level service without a Marketplace shop identifier, so
        # PiData allocates the accrued cost to every configured campaign.
        + total("product-banners") * max(campaign_count, 1),
        2,
    )
    # PiData treats the paid shelf as marketing alongside all promotion
    # formats.  It is not a storage charge despite its historical name.
    marketing = round(advertising + total("shelf", "payment"), 2)
    # ``Другие → полученные с МП`` is a distinct PiData article.  The
    # compact table combines that article with acquiring, but unrelated
    # service charges must not inflate the displayed value or be deducted a
    # second time from the projected transfer.
    other = marketplace_other
    payment_adjustments = round(
        total("loyalty_and_reviews", "customerBonusAmount")
        + total("paid_storage_after_01-06-22", "paidStorage")
        # The business report contains one manager row without a campaign
        # identifier. It is charged for each configured campaign, which is
        # also how PiData displays this service for a multi-campaign cabinet.
        + total("personal_manager") * max(campaign_count, 1)
        + total("reception_of_surplus")
        + total("export_from_warehouse")
        + total("order_processing")
        + total("order_processing_on_warehouse")
        + total("storage_of_returns"),
        2,
    )
    # PIData splits these figures into «Эквайринг» and «Другие». The compact
    # RBE table deliberately combines exactly those two articles in «Иное».
    acquiring = round(payment_transfer + paid_acceptance, 2)
    other_direct = round(acquiring + other, 2)
    direct = round(commission + logistics + acquiring, 2)
    # Use the buyer amount from the matching order + SKU line.  It is the
    # same basket as seller turnover, rather than one arbitrary line per
    # order (which undercounted multi-SKU orders).
    matched_buyer_rows = [row for row in payment_transfer_rows if row.get("buyerPaymentAmount") is not None]
    if matched_buyer_rows:
        buyer_buyout_turnover = round(
            sum(_number(row.get("buyerPaymentAmount")) for row in matched_buyer_rows), 2
        )
    else:
        # Compatibility for incomplete historical archives only. New imports
        # use the exact order + SKU link above.
        buyer_buyout_turnover = round(
            sum(
                _number(row.get("buyerPaymentAmount"))
                for row in order_sheets.get("orders_and_offers_transactions", [])
                if str(row.get("offerStatus") or "").strip() == "Доставлен покупателю"
            ),
            2,
        )
    returned_seller_turnover = round(
        sum(
            _number(
                row.get("sumBillingPriceOfItems")
                or row.get("partnerPriceForDelivery")
                or row.get("billingPrice")
            )
            for row in return_rows
        ),
        2,
    )
    returned_buyer_turnover = round(
        sum(
            abs(_number(row.get("refundBuyerPaymentAmount") or row.get("buyerPayment")))
            for row in return_rows
        ),
        2,
    )
    net_sales = round(
        sum(abs(_number(row.get("merchantPrice"))) for row in payment_transfer_rows)
        - returned_seller_turnover,
        2,
    )
    return {
        "sales": net_sales,
        "buyout_seller_turnover": net_sales,
        "buyout_buyer_turnover": round(buyer_buyout_turnover - returned_buyer_turnover, 2),
        "buyout_count": max(
            0,
            round(sum(_number(row.get("quantity")) for row in payment_transfer_rows))
            - round(sum(_number(row.get("count") or row.get("quantity") or 1) for row in return_rows)),
        ),
        "returned_count": sum(_number(row.get("count") or 1) for row in return_rows),
        "commission": commission,
        "commission_with_spp": commission,
        "spp": 0.0,
        "storage": None,
        "logistics": logistics,
        "paid_acceptance": paid_acceptance,
        "other_direct_expenses": other_direct,
        "direct_expenses": direct,
        "marketing": marketing,
        "advertising": advertising,
        "external_advertising": 0.0,
        "cabinet_advertising": advertising,
        "other": other,
        "unrecognized": 0.0,
        "cost_of_goods": None,
        "sold_cost_of_goods": None,
        "returned_cost_of_goods": None,
        "taxes": None,
        "vat": None,
        "bank_receipt": round(net_sales - direct - marketing - payment_adjustments, 2),
    }


def _source_day(value: object) -> str | None:
    """Return an ISO calendar day from either report date format."""

    text = str(value or "").strip()
    if len(text) >= 10 and text[4:5] == "-":
        return text[:10]
    try:
        return datetime.strptime(text[:10], "%d.%m.%Y").date().isoformat()
    except ValueError:
        return None


def _daily_service_sheets(service_sheets: dict[str, list[dict]], day: str) -> dict[str, list[dict]]:
    """Keep only service operations that belong to one calendar day."""

    return {
        sheet: [
            row
            for row in rows
            if _source_day(row.get("serviceDateTime") or row.get("serviceDate")) == day
        ]
        for sheet, rows in service_sheets.items()
    }


def _daily_order_sheets(order_sheets: dict[str, list[dict]], day: str) -> tuple[dict[str, list[dict]], list[dict]]:
    """Build one reporting day from Market's order facts.

    PiData shows a return in the period of the return event.  Market's
    ``statusChanged`` is the corresponding official event timestamp; the
    original delivery stays a separate sale event.
    """

    transactions = order_sheets.get("orders_and_offers_transactions", [])
    return_events: dict[tuple[str, str], dict] = {}
    for row in transactions:
        if not _is_financial_return(row):
            continue
        event_day = _source_day(row.get("statusChanged"))
        if not event_day:
            continue
        key = (str(row.get("orderId") or ""), str(row.get("shopSku") or row.get("offerId") or ""))
        previous = return_events.get(key)
        if previous is None or event_day < _source_day(previous.get("statusChanged")):
            return_events[key] = row
    daily_orders = {
        "orders_and_offers_transactions": [
            row for row in transactions if _source_day(row.get("deliveryDate")) == day
        ],
        # A non-redemption has no delivery date. Retain all statuses only for
        # excluding its technical payment-transfer operation from turnover.
        "payment_transfer_order_statuses": transactions,
        "returns": [row for row in return_events.values() if _source_day(row.get("statusChanged")) == day],
    }
    created_orders = [
        row for row in transactions if _source_day(row.get("creationDate")) == day
    ]
    return daily_orders, created_orders


def _apply_return_commission(values: dict[str, float | None], reversed_commission: float) -> None:
    """Apply an earlier placement-fee reversal to the day of the return."""

    if not reversed_commission:
        return
    values["commission"] = round(float(values["commission"] or 0) - reversed_commission, 2)
    values["commission_with_spp"] = values["commission"]
    values["direct_expenses"] = round(
        float(values["direct_expenses"] or 0) - reversed_commission, 2
    )
    values["bank_receipt"] = round(float(values["bank_receipt"] or 0) + reversed_commission, 2)


def _store_daily_pnl(
    store_slug: str,
    business_id: int,
    date_from: date,
    date_to: date,
    service_sheets: dict[str, list[dict]],
    order_sheets: dict[str, list[dict]],
    calculated_at: str,
    *,
    campaign_count: int = 1,
) -> None:
    """Persist facts per day; selected ranges are later just sums of these rows."""

    purchase_prices = {
        article: float(row["purchase_price"])
        for article, row in yandex_source_values.get_values(store_slug).items()
        if row.get("purchase_price") is not None
    }
    current_day = date_from
    while current_day <= date_to:
        day = current_day.isoformat()
        daily_orders, created_orders = _daily_order_sheets(order_sheets, day)
        daily_services = _daily_service_sheets(service_sheets, day)
        values = _pnl_values(
            daily_services,
            daily_orders,
            campaign_count=campaign_count,
        )
        return_commissions = yandex_financial.placement_commissions_by_order(
            store_slug,
            business_id,
            [row.get("orderId") for row in daily_orders["returns"]],
        )
        _apply_return_commission(values, round(sum(return_commissions.values()), 2))
        values["orders_turnover"] = round(
            sum(_number(row.get("partnerPriceForDelivery")) for row in created_orders), 2
        )
        # Cost of goods must use the exact same order lines as buyout
        # turnover.  Using Business Orders deliveries here while turnover
        # uses payment-transfer rows made the two indicators describe
        # different baskets of products.
        redeemed_items, returned_items = _buyout_basket(daily_services, daily_orders)
        sold_cost = _cost_for_items(redeemed_items, purchase_prices)
        returned_cost = _cost_for_items(returned_items, purchase_prices)
        if sold_cost is not None and returned_cost is not None:
            net_sold_cost = round(sold_cost - returned_cost, 2)
            values.update(
                {
                    "sold_cost_of_goods": net_sold_cost,
                    "returned_cost_of_goods": returned_cost,
                    "cost_of_goods": net_sold_cost,
                }
            )
        yandex_financial.replace_daily_pnl_summary(
            store_slug, business_id, day, values, calculated_at
        )
        current_day += timedelta(days=1)


def _store_daily_netting(
    store_slug: str,
    business_id: int,
    date_from: date,
    date_to: date,
    netting_sheets: dict[str, list[dict]],
    calculated_at: str,
) -> None:
    """Store daily settlement totals without replacing the PiData-style forecast.

    A settlement report describes movements under the payout schedule.  Its
    dates and deductions can relate to earlier sales, so its net amount is
    not the same as ``К перечислению на счёт`` for the selected sales period.
    Keep those facts for reconciliation, but leave ``bank_receipt`` as the
    calculation made by :func:`_pnl_values`.
    """

    daily: dict[str, dict[str, float]] = {}
    for rows in netting_sheets.values():
        for row in rows:
            transaction_day = _source_day(row.get("transactionDate"))
            transaction_sum = _number(row.get("transactionSum"))
            if transaction_day:
                values = daily.setdefault(
                    transaction_day, {"payment_accruals": 0.0, "payment_deductions": 0.0}
                )
                transaction_type = str(row.get("transactionType") or "").upper()
                if (
                    transaction_sum < 0
                    or "DEDUCTION" in transaction_type
                    or "WITHHOLD" in transaction_type
                    or "УДЕРЖ" in transaction_type
                ):
                    values["payment_deductions"] += abs(transaction_sum)
                else:
                    values["payment_accruals"] += transaction_sum
    current_day = date_from
    while current_day <= date_to:
        report_date = current_day.isoformat()
        values = daily.setdefault(report_date, {"payment_accruals": 0.0, "payment_deductions": 0.0})
        yandex_financial.update_daily_pnl_fields(
            store_slug,
            business_id,
            report_date,
            {key: round(value, 2) for key, value in values.items()},
            calculated_at,
        )
        current_day += timedelta(days=1)


def _report_rows(value: object):
    if isinstance(value, list):
        for item in value:
            yield from _report_rows(item)
    elif isinstance(value, dict):
        if any(key in value for key in ("orderId", "shopSku", "businessId", "partnerId")):
            yield value
        else:
            for item in value.values():
                yield from _report_rows(item)


def _download_archive(url: str) -> dict[str, list[dict]]:
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("Яндекс вернул небезопасную ссылку на финансовый отчёт")
    with urllib.request.urlopen(url, timeout=120) as response:
        content = response.read()
    sheets: dict[str, list[dict]] = {}
    with zipfile.ZipFile(BytesIO(content)) as archive:
        for name in archive.namelist():
            if not name.endswith(".json"):
                continue
            sheet = name.rsplit("/", 1)[-1][:-5]
            data = json.loads(archive.read(name).decode("utf-8-sig"))
            sheets[sheet] = list(_report_rows(data))
    return sheets


def _load_report(api_key: str, endpoint: str, payload: dict[str, object]) -> dict[str, list[dict]]:
    generated = api.request(
        endpoint,
        api_key,
        payload=payload,
        params={"format": "JSON", "language": "RU"},
    )
    report_id = generated.get("reportId")
    if not report_id:
        raise ValueError("Яндекс не вернул идентификатор финансового отчёта")
    for attempt in range(121):
        info = api.request(f"/v2/reports/info/{report_id}", api_key, method="GET")
        if info.get("status") == "DONE":
            return _download_archive(str(info.get("file") or ""))
        if info.get("status") == "FAILED":
            raise RuntimeError("Яндекс не смог сформировать финансовый отчёт")
        if attempt < 120:
            time.sleep(5)
    raise TimeoutError("Финансовый отчёт Яндекса ещё формируется")


def _load_united_orders_report(
    api_key: str, business_id: int, date_from: str, date_to: str, campaign_ids: list[int]
) -> dict[str, list[dict]]:
    payload: dict[str, object] = {"businessId": business_id, "dateFrom": date_from, "dateTo": date_to}
    if campaign_ids:
        payload["campaignIds"] = campaign_ids
    return _load_report(api_key, "/v2/reports/united-orders/generate", payload)


def _load_united_services_report(
    api_key: str, business_id: int, date_from: str, date_to: str, campaign_ids: list[int]
) -> dict[str, list[dict]]:
    """Load marketplace-service operations for one Business ID.

    The service-report endpoint accepts a Business ID only.  Its ``partnerId``
    is not guaranteed to be a campaign ID (notably for FBY services), so
    filtering the downloaded archive by configured campaign IDs can discard
    every valid operation for a cabinet.  The account itself defines the
    boundary here; campaign filtering is used only by endpoints that support
    it natively.
    """
    del campaign_ids
    return _load_report(
        api_key,
        "/v2/reports/united-marketplace-services/generate",
        {"businessId": business_id, "dateFrom": date_from, "dateTo": date_to},
    )


def _load_united_netting_report(
    api_key: str, business_id: int, date_from: str, date_to: str, campaign_ids: list[int]
) -> dict[str, list[dict]]:
    payload: dict[str, object] = {"businessId": business_id, "dateFrom": date_from, "dateTo": date_to}
    if campaign_ids:
        payload["campaignIds"] = campaign_ids
    return _load_report(api_key, "/v2/reports/united-netting/generate", payload)


def import_period(store_slug: str, date_from: date, date_to: date) -> dict:
    if date_to < date_from:
        raise ValueError("Дата окончания раньше даты начала")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    # The Business Orders endpoint is filtered by the order-creation date,
    # while the report displays delivery dates. Reload preceding orders as
    # well: an item created before the chosen period can be redeemed inside
    # it. This is also the source of SKU-level cost price.
    orders_sync = sales_sync.sync_store_period(
        store_slug,
        "YANDEX MARKET",
        date_from - timedelta(days=FINANCIAL_REPORT_LOOKBACK_DAYS),
        date_to + timedelta(days=1),
    )
    if not orders_sync.get("ok"):
        raise RuntimeError(
            "Не удалось загрузить заказы Яндекс Маркета: "
            f"{orders_sync.get('error') or '; '.join(orders_sync.get('warnings') or [])}"
        )
    report_rows: list[dict] = []
    imported: list[dict] = []
    for account in tokens.get_accounts(store_slug):
        business_id = int(account["business_id"])
        sheets = _load_united_orders_report(
            str(account["api_key"]),
            business_id,
            date_from.isoformat(),
            date_to.isoformat(),
            list(account.get("campaign_ids") or []),
        )
        service_sheets = _load_united_services_report(
            str(account["api_key"]),
            business_id,
            date_from.isoformat(),
            date_to.isoformat(),
            list(account.get("campaign_ids") or []),
        )
        netting_sheets = _load_united_netting_report(
            str(account["api_key"]),
            business_id,
            date_from.isoformat(),
            date_to.isoformat(),
            list(account.get("campaign_ids") or []),
        )
        report_sets = (
            (REPORT_KIND, sheets),
            (SERVICES_REPORT_KIND, service_sheets),
            (NETTING_REPORT_KIND, netting_sheets),
        )
        imported_report: dict[str, object] = {"business_id": business_id, "reports": {}}
        for report_kind, report_sheets in report_sets:
            report_rows = []
            for sheet, rows in report_sheets.items():
                for payload in rows:
                    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    report_rows.append(
                        {
                            "sheet": sheet,
                            "row_key": hashlib.sha256(encoded.encode()).hexdigest(),
                            "payload": payload,
                        }
                    )
            saved = yandex_financial.replace_report_rows(
                store_slug,
                business_id,
                report_kind,
                date_from.isoformat(),
                date_to.isoformat(),
                report_rows,
                now,
            )
            imported_report["reports"][report_kind] = {
                "rows": saved,
                "sheets": {name: len(rows) for name, rows in report_sheets.items()},
            }
            if report_kind == SERVICES_REPORT_KIND:
                yandex_financial.replace_pnl_summary(
                    store_slug,
                    business_id,
                    date_from.isoformat(),
                    date_to.isoformat(),
                    _pnl_values(
                        report_sheets,
                        sheets,
                        campaign_count=len(account.get("campaign_ids") or []),
                    ),
                    now,
                )
        imported.append(imported_report)
        yandex_financial.refresh_pnl_costs(store_slug, date_from.isoformat(), date_to.isoformat(), now)
        _store_daily_pnl(
            store_slug,
            business_id,
            date_from,
            date_to,
            service_sheets,
            sheets,
            now,
            campaign_count=len(account.get("campaign_ids") or []),
        )
        _store_daily_netting(store_slug, business_id, date_from, date_to, netting_sheets, now)
    if not imported:
        raise ValueError("Для магазина не настроен Business ID и API-ключ Яндекс Маркета")
    return {
        "store": store_slug,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "reports": imported,
        "orders": {"rows": orders_sync.get("rows", 0)},
    }


def sync_financial_reports(store_slug: str, current_day: date | None = None) -> dict:
    """Refresh the rolling financial-report window for one Yandex cabinet.

    Replacing the complete recent window keeps delayed service adjustments,
    returns and scheduled payment operations consistent without requiring a
    separate initial import for every newly connected store.
    """

    today = current_day or datetime.now(UTC).date()
    return import_period(
        store_slug,
        today - timedelta(days=FINANCIAL_REPORT_LOOKBACK_DAYS),
        today,
    )
