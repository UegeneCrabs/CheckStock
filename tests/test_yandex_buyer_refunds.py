import json
import sqlite3

import pytest

from app.yandex.buyer_refunds import build_refunds
from app.yandex.financial_reports import _cost_for_items, _daily_order_sheets, _pnl_values
from app.repositories import yandex_financial as repository


def order(**changes):
    return {"orderId": 1, "shopSku": "sku", "offerStatus": "Возврат принят на складе",
            "deliveryDate": "01.10.2026", "statusChanged": "2026-10-12 14:00:00",
            "billingPrice": 5752, "deliveredOrReturned": 1, **changes}


def payment(**changes):
    return {"orderId": 1, "shopSku": "sku", "transactionDate": "11.10.2026 18:53",
            "transactionType": "Возврат", "transactionSource": "Возврат платежа покупателя",
            "transactionSum": -2339, "count": 1, "transactionId": None, **changes}


def events(orders=None, payments=None):
    return build_refunds(orders or [order()], payments if payments is not None else [payment()],
                         "2026-10-05", "2026-10-11")


def test_trust_points_only_is_not_a_refund():
    assert events(payments=[payment(transactionSource="Возврат баллов за скидку Маркета",
                                    transactionSum=-3413)]) == []


def test_buyer_money_dates_both_turnovers_count_and_costs_together():
    ee = events(payments=[payment(), payment(actDate="new report"),
                         payment(transactionSource="Возврат баллов за скидку Маркета")])
    assert len(ee) == 1
    daily, _ = _daily_order_sheets({"orders_and_offers_transactions": [order()]}, "2026-10-11", ee)
    values = _pnl_values({}, daily)
    assert values["buyout_seller_turnover"] == -5752
    assert values["buyout_buyer_turnover"] == -2339
    assert values["returned_count"] == 1
    assert values["buyout_count"] == -1  # no clipping: weekly sums remain additive
    assert _cost_for_items(daily["returns"], {"sku": 500}) == 500
    assert _daily_order_sheets({}, "2026-10-12", ee)[0]["returns"] == []


def test_tris_warehouse_acceptance_does_not_move_september_money_to_october():
    assert events([order(shopSku="304329519", billingPrice=3871, statusChanged="2026-10-05")],
                  [payment(shopSku="304329519", transactionDate="28.09.2026 07:03",
                           transactionSum=-1951)]) == []


def test_nonpurchase_money_is_not_returned_sales():
    assert events([order(offerStatus="Невыкуп принят на складе", deliveryDate=None)]) == []


def test_multiple_units_and_partial_returns_have_correct_money_and_quantity():
    ee = events([order(deliveredOrReturned=3)], [payment(count=2, transactionSum=-4678),
        payment(count=1, transactionDate="10.10.2026 10:00", transactionSum=-2339)])
    assert sum(e["count"] for e in ee) == 3
    assert sum(e["sumBillingPriceOfItems"] for e in ee) == 5752 * 3
    assert _cost_for_items(ee, {"sku": 500}) == 1500


def test_ambiguous_repeated_payment_cannot_double_count_a_single_item():
    with pytest.raises(ValueError, match="превышает"):
        events(payments=[payment(transactionId="a"), payment(transactionId="b")])


def test_missing_quantity_or_ambiguous_amount_is_not_assumed_to_be_one():
    with pytest.raises(ValueError, match="количества"):
        events(payments=[payment(count=None)])
    with pytest.raises(ValueError, match="Неоднозначная"):
        events(payments=[payment(), payment(transactionSum=-2000)])


def test_transaction_id_deduplicates_metadata_but_not_different_skus():
    ee = events([order(), order(shopSku="two", billingPrice=100)],
        [payment(transactionId=1), payment(transactionId=1, transactionDate="11.10.2026 19:00"),
         payment(transactionId=1, shopSku="two", transactionSum=-50)])
    assert len(ee) == 2


@pytest.fixture
def archive():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE yandex_financial_report_rows (
        store_slug TEXT, business_id INTEGER, report_kind TEXT, sheet TEXT,
        payload_json TEXT, period_from TEXT, period_to TEXT, imported_at TEXT)""")
    yield conn
    conn.close()


def save(conn, kind, payload, *, store="tris", business=1, version="2026-10-12", start="2026-10-05",
         end="2026-10-11", sheet=None):
    conn.execute("INSERT INTO yandex_financial_report_rows VALUES (?,?,?,?,?,?,?,?)",
        (store, business, kind, sheet or ("transaction_date" if kind == "united-netting" else
         "orders_and_offers_transactions"), json.dumps(payload), start, end, version))


def test_overlapping_exports_and_amount_correction_use_latest_only(archive):
    save(archive, "united-orders", order())
    save(archive, "united-netting", payment())
    save(archive, "united-netting", payment(transactionSum=-2000), version="2026-10-13", start="2026-10-01")
    ee = repository.buyer_refund_events("tris", 1, "2026-10-05", "2026-10-11", source=archive)
    assert len(ee) == 1 and ee[0]["refundBuyerPaymentAmount"] == -2000
    # Loading an overlapping snapshot again remains idempotent.
    save(archive, "united-netting", payment(transactionSum=-2000), version="2026-10-13", start="2026-10-01")
    assert len(repository.buyer_refund_events("tris", 1, "2026-10-05", "2026-10-11", source=archive)) == 1


def test_empty_new_export_removes_stale_refund(archive):
    save(archive, "united-orders", order())
    save(archive, "united-netting", payment())
    save(archive, "united-netting", {}, version="2026-10-13", sheet="__coverage__")
    assert repository.buyer_refund_events("tris", 1, "2026-10-05", "2026-10-11", source=archive) == []


def test_corrected_transaction_date_does_not_survive_in_an_older_window(archive):
    save(archive, "united-orders", order())
    save(archive, "united-netting", payment(transactionId="refund-1"))
    save(archive, "united-netting", payment(transactionId="refund-1", transactionDate="12.10.2026 10:00"),
         version="2026-10-13", start="2026-10-12", end="2026-10-12")
    assert repository.buyer_refund_events("tris", 1, "2026-10-05", "2026-10-11", source=archive) == []
    assert len(repository.buyer_refund_events("tris", 1, "2026-10-12", "2026-10-12", source=archive)) == 1


def test_old_operation_repeated_outside_new_export_period_is_not_lost(archive):
    save(archive, "united-orders", order())
    save(archive, "united-netting", payment(transactionId="refund-1"))
    save(archive, "united-netting", payment(transactionId="refund-1"),
         version="2026-10-13", start="2026-10-12", end="2026-10-12")
    assert len(repository.buyer_refund_events("tris", 1, "2026-10-05", "2026-10-11", source=archive)) == 1


def test_daily_rebuild_is_idempotent_and_updates_all_refund_metrics(archive, monkeypatch):
    from datetime import date
    from app.yandex import financial_reports as reports

    save(archive, "united-orders", order())
    save(archive, "united-netting", payment())
    saved = {}
    reader = repository.buyer_refund_events
    monkeypatch.setattr(repository, "buyer_refund_events", lambda s, b, f, t:
                        reader(s, b, f, t, source=archive))
    monkeypatch.setattr(repository, "buyer_refund_commission", lambda *args: 0)
    monkeypatch.setattr(reports.yandex_source_values, "get_values", lambda store:
                        {"sku": {"purchase_price": 500}})
    monkeypatch.setattr(repository, "replace_daily_pnl_summary", lambda s, b, d, v, now:
                        saved.__setitem__((s, b, d), v))
    for _ in range(2):
        reports._store_daily_pnl("tris", 1, date(2026, 10, 10), date(2026, 10, 12), {},
                                 {"orders_and_offers_transactions": [order()]}, "now")
    assert len(saved) == 3
    assert saved[("tris", 1, "2026-10-11")]["returned_count"] == 1
    assert saved[("tris", 1, "2026-10-11")]["cost_of_goods"] == -500
    assert saved[("tris", 1, "2026-10-12")]["returned_count"] == 0
    assert sum(v["buyout_seller_turnover"] for v in saved.values()) == -5752


def test_refund_commission_is_per_sku_and_quantity(tmp_path, monkeypatch):
    path = tmp_path / "commission.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE yandex_financial_report_rows (store_slug, business_id, report_kind, sheet, payload_json, imported_at)")
    for sku, amount, count in [("sku", 300, 3), ("other", 500, 1)]:
        conn.execute("INSERT INTO yandex_financial_report_rows VALUES (?,?,?,?,?,?)",
                     ("tris", 1, "united-services", "placement", json.dumps(
                         {"orderId": 1, "shopSku": sku, "amountWithoutBonuses": amount, "count": count}), "now"))
    conn.commit()
    conn.close()
    def connect():
        result = sqlite3.connect(path)
        result.row_factory = sqlite3.Row
        return result
    monkeypatch.setattr(repository, "get_connection", connect)
    assert repository.buyer_refund_commission("tris", 1, [{"orderId": 1, "shopSku": "sku", "count": 1}]) == 100


@pytest.mark.parametrize("store", ["tris", "rimili", "trusthome", "sokoloff", "rockkiddo", "gogol"])
def test_store_and_business_isolation(archive, store):
    save(archive, "united-orders", order(), store=store)
    save(archive, "united-netting", payment(), store=store)
    save(archive, "united-netting", payment(transactionSum=-9999), store=store, business=2)
    assert repository.buyer_refund_events(store, 1, "2026-10-05", "2026-10-11", source=archive)[0]["refundBuyerPaymentAmount"] == -2339
