"""Storage for raw Yandex financial report rows and their P&L aggregates."""

import json
from collections import defaultdict
from datetime import datetime

from app.repositories.core import WRITE_LOCK, get_connection

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


def replace_report_rows(
    store_slug: str,
    business_id: int,
    report_kind: str,
    date_from: str,
    date_to: str,
    rows: list[dict],
    imported_at: str,
) -> int:
    # A generated Yandex archive can contain byte-for-byte identical rows.
    # Their natural key is the sheet plus the payload hash, which is also the
    # database primary key for a report row.
    unique_rows = list({(str(row["sheet"]), str(row["row_key"])): row for row in rows}.values())
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                "DELETE FROM yandex_financial_report_rows "
                "WHERE store_slug=? AND business_id=? AND report_kind=? AND period_from=? AND period_to=?",
                (store_slug, business_id, report_kind, date_from, date_to),
            )
            conn.executemany(
                """
                INSERT INTO yandex_financial_report_rows
                (store_slug,business_id,report_kind,sheet,row_key,period_from,period_to,payload_json,imported_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        store_slug,
                        business_id,
                        report_kind,
                        row["sheet"],
                        # One financial operation may legitimately be present
                        # in several date-range reports. Make its storage key
                        # unique per imported period while retaining the
                        # payload hash as its stable suffix.
                        f"{date_from}:{date_to}:{row['row_key']}",
                        date_from,
                        date_to,
                        json.dumps(row["payload"], ensure_ascii=False, separators=(",", ":")),
                        imported_at,
                    )
                    for row in unique_rows
                ],
            )
            conn.commit()
        finally:
            conn.close()
    return len(unique_rows)


def financial_summary(date_from: str, date_to: str, store_slug: str | None = None) -> dict | None:
    """Aggregate the Yandex order-finance sheet for an exactly imported period."""

    where = (
        "period_from=? AND period_to=? AND report_kind='united-orders' AND sheet='services_and_orders_margin'"
    )
    params: list[object] = [date_from, date_to]
    if store_slug:
        where += " AND store_slug=?"
        params.append(store_slug)
    conn = get_connection()
    rows = conn.execute(
        f"SELECT payload_json FROM yandex_financial_report_rows WHERE {where}", params
    ).fetchall()
    conn.close()
    if not rows:
        return None

    totals: defaultdict[str, float] = defaultdict(float)
    count = 0
    keys = (
        "sumBillingPriceOfItems",
        "buyerPayment",
        "incomeWithoutServices",
        "summaryCommission",
        "saleCommission",
        "warehouseProcessing",
        "buyerDelivery",
        "crossregionalDelivery",
        "buyerExpressDelivery",
        "crossborderDelivery",
        "buyerPaymentAccept",
        "buyerPaymentTransfer",
        "orderIntake",
        "orderProcessing",
        "resupplyHandling",
        "returnResupply",
        "loyaltyProgram",
        "boost",
        "installment",
    )
    for row in rows:
        payload = json.loads(row["payload_json"])
        count += 1
        for key in keys:
            try:
                totals[key] += float(payload.get(key) or 0)
            except (TypeError, ValueError):
                continue
    logistics = sum(
        abs(totals[key])
        for key in ("buyerDelivery", "crossregionalDelivery", "buyerExpressDelivery", "crossborderDelivery")
    )
    return {
        "rows": count,
        "sales": totals["sumBillingPriceOfItems"],
        "commission": abs(totals["saleCommission"]),
        "commission_with_spp": abs(totals["summaryCommission"]),
        "logistics": logistics,
        "paid_acceptance": abs(totals["buyerPaymentAccept"]),
        "other_direct_expenses": sum(
            abs(totals[key])
            for key in (
                "warehouseProcessing",
                "buyerPaymentTransfer",
                "orderIntake",
                "orderProcessing",
                "resupplyHandling",
                "returnResupply",
            )
        ),
        "marketing": sum(abs(totals[key]) for key in ("loyaltyProgram", "boost", "installment")),
        "payout": totals["incomeWithoutServices"],
        "bank_receipt": totals["incomeWithoutServices"],
    }


def replace_pnl_summary(
    store_slug: str, business_id: int, date_from: str, date_to: str, values: dict, calculated_at: str
) -> None:
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO yandex_financial_pnl
                (store_slug,business_id,period_from,period_to,values_json,calculated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(store_slug,business_id,period_from,period_to) DO UPDATE SET
                    values_json=excluded.values_json, calculated_at=excluded.calculated_at
                """,
                (store_slug, business_id, date_from, date_to, json.dumps(values), calculated_at),
            )
            conn.commit()
        finally:
            conn.close()


def pnl_summary(date_from: str, date_to: str, store_slug: str | None = None) -> dict | None:
    """Sum daily financial facts for an inclusive date range.

    The legacy range snapshot is used only until a period has been backfilled
    into daily facts.
    """
    # The visible report is grouped by the actual day of an operation. A
    # range archive can already contain returns that occur after its end, so
    # its final statuses must not rewrite a completed historical day.
    where = "report_date>=? AND report_date<=?"
    params = [date_from, date_to]
    if store_slug:
        where += " AND store_slug=?"
        params.append(store_slug)
    conn = get_connection()
    rows = conn.execute(f"SELECT values_json FROM yandex_financial_daily_pnl WHERE {where}", params).fetchall()
    conn.close()
    if rows:
        return _sum_values(rows)

    # Legacy fallback for periods imported before daily facts existed.
    where = "period_from=? AND period_to=?"
    params = [date_from, date_to]
    if store_slug:
        where += " AND store_slug=?"
        params.append(store_slug)
    conn = get_connection()
    range_rows = conn.execute(f"SELECT values_json FROM yandex_financial_pnl WHERE {where}", params).fetchall()
    conn.close()
    return _sum_values(range_rows) if range_rows else None


def financial_return_count(date_from: str, date_to: str, store_slugs: list[str]) -> int:
    """Count completed financial returns from the stored source archive.

    Daily P&L snapshots created before return tracking was introduced do not
    contain ``returned_count``. The raw united-orders archive is retained, so
    it is safe to use it as the source for both old and new reporting periods.
    PiData reports a return in the period in which the return event occurred,
    not in the earlier period of the original delivery.  ``statusChanged`` is
    the event timestamp supplied by the official Marketplace report.
    Overlapping report downloads contain the same operation repeatedly; the
    return key prevents counting it more than once.
    """

    if not store_slugs:
        return 0
    placeholders = ", ".join("?" for _ in store_slugs)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT payload_json
            FROM yandex_financial_report_rows
            WHERE report_kind='united-orders'
              AND sheet='orders_and_offers_transactions'
              AND store_slug IN ({placeholders})
            """,
            store_slugs,
        ).fetchall()
    finally:
        conn.close()

    # A marketplace can emit several lifecycle statuses for the same return.
    # PiData records it once, on the first return event.  Keep that earliest
    # event instead of summing every later logistics confirmation.
    events: dict[tuple[str, str], tuple[str, int]] = {}
    for row in rows:
        payload = json.loads(row["payload_json"])
        status = str(payload.get("offerStatus") or "").strip()
        event_day = _source_day(payload.get("statusChanged"))
        if not event_day:
            continue
        has_financial_refund = _number(payload.get("refundBuyerPaymentAmount")) != 0
        delivered = _source_day(payload.get("deliveryDate")) is not None
        is_return = status in _REPORTABLE_RETURN_OFFER_STATUSES or (
            status in _FINANCIALLY_REVERSED_OFFER_STATUSES and delivered and has_financial_refund
        )
        if not is_return:
            continue
        event_key = (str(payload.get("orderId") or ""), str(payload.get("shopSku") or payload.get("offerId") or ""))
        candidate = (event_day, _as_quantity(payload.get("count")))
        if event_key not in events or candidate[0] < events[event_key][0]:
            events[event_key] = candidate
    return sum(quantity for event_day, quantity in events.values() if date_from <= event_day <= date_to)


def financial_delivery_count(date_from: str, date_to: str, store_slugs: list[str]) -> int:
    """Count delivered order lines from the latest financial-report snapshot.

    Business Orders is queried by creation date and may temporarily omit an
    old order that was delivered in the selected period.  The financial
    archive is an independent confirmation of that delivery.  Keep only its
    latest copy because rolling report downloads contain the same operation
    repeatedly.
    """

    if not store_slugs:
        return 0
    placeholders = ", ".join("?" for _ in store_slugs)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT store_slug, payload_json, imported_at
            FROM yandex_financial_report_rows
            WHERE report_kind='united-orders'
              AND sheet='orders_and_offers_transactions'
              AND store_slug IN ({placeholders})
            ORDER BY imported_at
            """,
            store_slugs,
        ).fetchall()
    finally:
        conn.close()

    latest: dict[tuple[str, str, str, str], dict] = {}
    for row in rows:
        payload = json.loads(row["payload_json"])
        delivery_day = _source_day(payload.get("deliveryDate"))
        if not delivery_day or delivery_day < date_from or delivery_day > date_to:
            continue
        # One order can contain several distinct SKU lines. The financial
        # archive repeats an identical line in each rolling download.
        key = (
            str(row["store_slug"]),
            str(payload.get("orderId") or ""),
            str(payload.get("shopSku") or payload.get("offerId") or ""),
            delivery_day,
        )
        latest[key] = payload
    return sum(_as_quantity(payload.get("count")) for payload in latest.values())


def financial_buyout_count(date_from: str, date_to: str, store_slugs: list[str]) -> int | None:
    """Return the completed-buyout quantity from an exact financial export.

    ``payment_transfer`` is the financial confirmation of a completed
    redemption.  Unlike the Business Orders endpoint it is not limited by an
    order's creation date, and the export already accounts for the return and
    cancellation state used by the marketplace financial report.  Use it only
    when every requested store has an export for the exact selected period;
    mixing an exact export with a heuristic fallback would make the total
    depend on which stores happened to be refreshed most recently.
    """

    if not store_slugs:
        return None
    placeholders = ", ".join("?" for _ in store_slugs)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT store_slug, payload_json
            FROM yandex_financial_report_rows
            WHERE report_kind='united-services'
              AND sheet='payment_transfer'
              AND period_from=? AND period_to=?
              AND store_slug IN ({placeholders})
            """,
            [date_from, date_to, *store_slugs],
        ).fetchall()
    finally:
        conn.close()

    covered_stores = {str(row["store_slug"]) for row in rows}
    if covered_stores != set(store_slugs):
        return None
    return sum(_as_quantity(json.loads(row["payload_json"]).get("count")) for row in rows)


def rebuild_daily_buyout_counts(store_slug: str, date_from: str, date_to: str) -> int:
    """Backfill daily P&L quantities with PiData-style source events.

    Sales are read from stored official Business Orders events. Returns use
    the status-change date in the official financial archive.  Monetary
    fields remain untouched because they are settled by other report sheets.
    """

    conn = get_connection()
    try:
        sales_rows = conn.execute(
            """
            SELECT substr(sold_at, 1, 10) AS day, SUM(sold_quantity) AS quantity
            FROM sales_order_lines
            WHERE store_slug=? AND marketplace='YANDEX MARKET'
              AND sold_at>=? AND sold_at<?
            GROUP BY substr(sold_at, 1, 10)
            """,
            (store_slug, date_from, f"{date_to}T23:59:59"),
        ).fetchall()
        sales_by_day = {str(row["day"]): _as_quantity(row["quantity"]) for row in sales_rows}
        raw_rows = conn.execute(
            """
            SELECT payload_json FROM yandex_financial_report_rows
            WHERE store_slug=? AND report_kind='united-orders'
              AND sheet='orders_and_offers_transactions'
            """,
            (store_slug,),
        ).fetchall()
        return_events: dict[tuple[str, str], tuple[str, int]] = {}
        for row in raw_rows:
            payload = json.loads(row["payload_json"])
            event_day = _source_day(payload.get("statusChanged"))
            if not event_day:
                continue
            status = str(payload.get("offerStatus") or "").strip()
            delivered = _source_day(payload.get("deliveryDate")) is not None
            has_financial_refund = _number(payload.get("refundBuyerPaymentAmount")) != 0
            if status not in _REPORTABLE_RETURN_OFFER_STATUSES and not (
                status in _FINANCIALLY_REVERSED_OFFER_STATUSES and delivered and has_financial_refund
            ):
                continue
            key = (str(payload.get("orderId") or ""), str(payload.get("shopSku") or payload.get("offerId") or ""))
            candidate = (event_day, _as_quantity(payload.get("count")))
            if key not in return_events or candidate[0] < return_events[key][0]:
                return_events[key] = candidate
        returns_by_day: defaultdict[str, int] = defaultdict(int)
        for event_day, quantity in return_events.values():
            if date_from <= event_day <= date_to:
                returns_by_day[event_day] += quantity

        rows = conn.execute(
            """
            SELECT report_date, values_json FROM yandex_financial_daily_pnl
            WHERE store_slug=? AND report_date>=? AND report_date<=?
            """,
            (store_slug, date_from, date_to),
        ).fetchall()
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        updates = []
        for row in rows:
            day = str(row["report_date"])
            values = json.loads(row["values_json"])
            values["buyout_count"] = max(0, sales_by_day.get(day, 0) - returns_by_day.get(day, 0))
            updates.append((json.dumps(values), now, store_slug, day))
        if updates:
            conn.executemany(
                "UPDATE yandex_financial_daily_pnl SET values_json=?, calculated_at=? WHERE store_slug=? AND report_date=?",
                updates,
            )
            conn.commit()
        return len(updates)
    finally:
        conn.close()


def placement_commissions_by_order(
    store_slug: str, business_id: int, order_ids: list[object]
) -> dict[object, float]:
    """Return the original placement commission for completed return orders."""

    wanted = {order_id for order_id in order_ids if order_id is not None}
    if not wanted:
        return {}
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM yandex_financial_report_rows
            WHERE store_slug=? AND business_id=?
              AND report_kind='united-services' AND sheet='placement'
            """,
            (store_slug, business_id),
        ).fetchall()
    finally:
        conn.close()
    result: dict[object, float] = {}
    for row in rows:
        payload = json.loads(row["payload_json"])
        order_id = payload.get("orderId")
        if order_id not in wanted:
            continue
        amount = _number(payload.get("amountWithoutBonuses"))
        if amount > 0:
            result[order_id] = max(result.get(order_id, 0.0), amount)
    return result


def daily_bank_receipts(date_from: str, date_to: str) -> list[dict]:
    """Return daily bank-receipt facts grouped by store for reporting tables."""

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT store_slug, report_date, values_json
            FROM yandex_financial_daily_pnl
            WHERE report_date>=? AND report_date<=?
            ORDER BY store_slug, report_date
            """,
            (date_from, date_to),
        ).fetchall()
    finally:
        conn.close()
    result: list[dict] = []
    for row in rows:
        values = json.loads(row["values_json"])
        value = values.get("bank_receipt")
        if value is None:
            continue
        try:
            result.append(
                {
                    "store_slug": str(row["store_slug"]),
                    "report_date": str(row["report_date"]),
                    "bank_receipt": float(value),
                }
            )
        except (TypeError, ValueError):
            continue
    return result


def replace_daily_pnl_summary(
    store_slug: str, business_id: int, report_date: str, values: dict, calculated_at: str
) -> None:
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO yandex_financial_daily_pnl
                (store_slug,business_id,report_date,values_json,calculated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(store_slug,business_id,report_date) DO UPDATE SET
                    values_json=excluded.values_json, calculated_at=excluded.calculated_at
                """,
                (store_slug, business_id, report_date, json.dumps(values), calculated_at),
            )
            conn.commit()
        finally:
            conn.close()


def update_daily_pnl_fields(
    store_slug: str,
    business_id: int,
    report_date: str,
    values: dict,
    calculated_at: str,
) -> None:
    """Update selected daily facts without erasing facts from another Yandex report."""

    with WRITE_LOCK:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT values_json FROM yandex_financial_daily_pnl "
                "WHERE store_slug=? AND business_id=? AND report_date=?",
                (store_slug, business_id, report_date),
            ).fetchone()
            stored = json.loads(row["values_json"]) if row else {}
            stored.update(values)
            conn.execute(
                """
                INSERT INTO yandex_financial_daily_pnl
                (store_slug,business_id,report_date,values_json,calculated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(store_slug,business_id,report_date) DO UPDATE SET
                    values_json=excluded.values_json, calculated_at=excluded.calculated_at
                """,
                (store_slug, business_id, report_date, json.dumps(stored), calculated_at),
            )
            conn.commit()
        finally:
            conn.close()


def _sum_values(rows: list) -> dict:
    totals: defaultdict[str, float] = defaultdict(float)
    # A partial cost price is more misleading than an empty one.  In
    # particular, summing seven days while silently dropping the day whose
    # sold SKU has no purchase price produces an understated COGS figure.
    incomplete_cost_keys: set[str] = set()
    cost_keys = {"cost_of_goods", "sold_cost_of_goods", "returned_cost_of_goods"}
    for row in rows:
        for key, value in json.loads(row["values_json"]).items():
            if value is not None:
                totals[key] += float(value)
            elif key in cost_keys:
                incomplete_cost_keys.add(key)
    result = dict(totals)
    for key in incomplete_cost_keys:
        result[key] = None
    return result


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _source_day(value: object) -> str | None:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4:5] == "-":
        return text[:10]
    try:
        return datetime.strptime(text[:10], "%d.%m.%Y").date().isoformat()
    except ValueError:
        return None


def refresh_pnl_costs(store_slug: str, date_from: str, date_to: str, calculated_at: str) -> dict:
    """Attach purchase costs to saved P&L snapshots from the source-price table."""

    conn = get_connection()
    try:
        prices = {
            str(row["article"]): float(row["purchase_price"])
            for row in conn.execute(
                "SELECT article,purchase_price FROM unit_economics_yandex_source_values "
                "WHERE store_slug=? AND purchase_price IS NOT NULL",
                (store_slug,),
            )
        }
        rows = conn.execute(
            """
            SELECT business_id,report_kind,sheet,payload_json
            FROM yandex_financial_report_rows
            WHERE store_slug=? AND period_from=? AND period_to=?
              AND ((report_kind='united-services' AND sheet='payment_transfer')
                   OR (report_kind='united-orders' AND sheet='services_and_orders_margin'))
            """,
            (store_slug, date_from, date_to),
        ).fetchall()
        summaries = conn.execute(
            "SELECT business_id,values_json FROM yandex_financial_pnl "
            "WHERE store_slug=? AND period_from=? AND period_to=?",
            (store_slug, date_from, date_to),
        ).fetchall()
    finally:
        conn.close()

    costs: dict[int, dict[str, object]] = defaultdict(
        lambda: {"sold": 0.0, "returned": 0.0, "missing": set()}
    )
    for row in rows:
        payload = json.loads(row["payload_json"])
        sku = str(payload.get("shopSku") or "").strip()
        if not sku:
            continue
        price = prices.get(sku)
        bucket = costs[int(row["business_id"])]
        count = _as_quantity(payload.get("count"))
        if row["report_kind"] == "united-services":
            if price is None:
                bucket["missing"].add(sku)
            else:
                bucket["sold"] += price * count
        elif str(payload.get("orderStatus") or "").startswith("Полный возврат"):
            if price is None:
                bucket["missing"].add(sku)
            else:
                bucket["returned"] += price * count

    updated = 0
    for row in summaries:
        business_id = int(row["business_id"])
        values = json.loads(row["values_json"])
        bucket = costs[business_id]
        if not bucket["missing"]:
            sold = round(float(bucket["sold"]), 2)
            returned = round(float(bucket["returned"]), 2)
            values.update(
                {
                    "sold_cost_of_goods": sold,
                    "returned_cost_of_goods": returned,
                    "cost_of_goods": round(sold - returned, 2),
                }
            )
        replace_pnl_summary(store_slug, business_id, date_from, date_to, values, calculated_at)
        updated += 1
    return {
        "updated": updated,
        "missing_articles": sorted({x for item in costs.values() for x in item["missing"]}),
    }


def _as_quantity(value: object) -> int:
    try:
        return max(int(float(value or 1)), 1)
    except (TypeError, ValueError):
        return 1
