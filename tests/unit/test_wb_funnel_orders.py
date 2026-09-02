import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from app import db
from app.dto.unit_economics_1c import UnitEconomics1CCabinetSettingsRequest
from app.repositories import core
from app.wb import funnel_orders


def _response(
    *,
    article: str = "1001",
    vendor_code: str = "RK-1001",
    name: str = "Тестовый товар",
    orders: int,
    amount: float,
    cancellations: int = 0,
    cancellation_amount: float = 0,
    buyouts: int = 0,
    buyout_amount: float = 0,
    buyout_percent: float = 0,
    cursor: str = "",
) -> dict:
    return {
        "data": {
            "products": [
                {
                    "product": {"nmId": int(article), "vendorCode": vendor_code, "title": name},
                    "statistic": {
                        "selected": {
                            "orderCount": orders,
                            "orderSum": amount,
                            "cancelCount": cancellations,
                            "cancelSum": cancellation_amount,
                            "buyoutCount": buyouts,
                            "buyoutSum": buyout_amount,
                            "conversions": {"buyoutPercent": buyout_percent},
                        }
                    },
                }
            ],
            **({"cursor": cursor} if cursor else {}),
        }
    }


class WbFunnelOrdersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = mock.patch.object(core, "DB_PATH", Path(self.temp.name) / "funnel.sqlite3")
        self.path_patch.start()
        db.init_db()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temp.cleanup()

    def test_sync_store_persists_orders_cancellations_and_net_values_separately(self) -> None:
        first_day = date.today() - timedelta(days=1)
        second_day = date.today()
        with (
            mock.patch.object(funnel_orders, "_days_to_sync", return_value=[first_day, second_day]),
            mock.patch.object(funnel_orders.wb_tokens, "has_token", return_value=True),
            mock.patch.object(funnel_orders.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(
                funnel_orders.wb_api,
                "request",
                side_effect=[
                    _response(orders=10, amount=1_500, cancellations=2, cancellation_amount=300),
                    _response(orders=7, amount=700, cancellations=1, cancellation_amount=100),
                ],
            ) as request,
        ):
            result = funnel_orders.sync_store("RIMILI")

        self.assertEqual(result, {"store": "rimili", "status": "success", "records": 2})
        payload = funnel_orders.dashboard(first_day.isoformat(), second_day.isoformat(), "rimili")
        self.assertEqual(
            payload["series"],
            [
                {
                    "date": first_day.isoformat(),
                    "orders_count": 10,
                    "orders_amount": 1_500.0,
                    "cancel_count": 2,
                    "cancel_amount": 300.0,
                    "net_orders_count": 8,
                    "net_orders_amount": 1_200.0,
                },
                {
                    "date": second_day.isoformat(),
                    "orders_count": 7,
                    "orders_amount": 700.0,
                    "cancel_count": 1,
                    "cancel_amount": 100.0,
                    "net_orders_count": 6,
                    "net_orders_amount": 600.0,
                },
            ],
        )
        body = request.call_args_list[0].kwargs["json_body"]
        self.assertEqual(
            body["selectedPeriod"], {"start": first_day.isoformat(), "end": first_day.isoformat()}
        )
        self.assertEqual(
            body["pastPeriod"],
            {
                "start": (first_day - timedelta(days=1)).isoformat(),
                "end": (first_day - timedelta(days=1)).isoformat(),
            },
        )
        self.assertNotIn("offset", body)

    def test_sync_store_keeps_each_article_separately_and_follows_cursor(self) -> None:
        day = date.today()
        first_page = _response(
            article="1001", orders=5, amount=500, cancellations=1, cancellation_amount=100,
            buyouts=3, buyout_amount=330, buyout_percent=75, cursor="next-page"
        )
        second_page = _response(
            article="1002", orders=3, amount=300, cancellations=0, cancellation_amount=0,
            buyouts=2, buyout_amount=220, buyout_percent=50,
        )
        with (
            mock.patch.object(funnel_orders, "_days_to_sync", return_value=[day]),
            mock.patch.object(funnel_orders.wb_tokens, "has_token", return_value=True),
            mock.patch.object(funnel_orders.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(
                funnel_orders.wb_api, "request", side_effect=[first_page, second_page]
            ) as request,
        ):
            result = funnel_orders.sync_store("rimili")

        self.assertEqual(result, {"store": "rimili", "status": "success", "records": 2})
        self.assertEqual(request.call_args_list[1].kwargs["json_body"]["cursor"], "next-page")
        conn = db.get_connection()
        try:
            rows = conn.execute(
                "SELECT article, vendor_code, product_name, orders_count, orders_amount, "
                "cancel_count, cancel_amount, buyout_count, buyout_amount, buyout_percent, source_version "
                "FROM wb_funnel_daily_orders ORDER BY article"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(
            [tuple(row.values()) for row in rows],
            [
                ("1001", "RK-1001", "Тестовый товар", 5, 500.0, 1, 100.0, 3, 330.0, 75.0, 4),
                ("1002", "RK-1001", "Тестовый товар", 3, 300.0, 0, 0.0, 2, 220.0, 50.0, 4),
            ],
        )

    def test_dashboard_aggregates_all_stores(self) -> None:
        day = date.today()
        funnel_orders._replace_day("rimili", day, [("1001", "RK-1", "Товар 1", 4, 500)])
        funnel_orders._replace_day("tris", day, [("1002", "TR-1", "Товар 2", 6, 900)])

        payload = funnel_orders.dashboard(day.isoformat(), day.isoformat())

        self.assertEqual(payload["store"], "all")
        self.assertEqual(
            payload["series"],
            [
                {
                    "date": day.isoformat(),
                    "orders_count": 10,
                    "orders_amount": 1_400.0,
                    "cancel_count": 0,
                    "cancel_amount": 0.0,
                    "net_orders_count": 10,
                    "net_orders_amount": 1_400.0,
                }
            ],
        )

    def test_product_sales_starts_use_first_day_with_orders(self) -> None:
        today = date.today()
        first_day = today - timedelta(days=5)
        later_day = today - timedelta(days=2)
        funnel_orders._replace_day("rimili", first_day, [("1001", "RK-1", "Товар 1", 0, 0)])
        funnel_orders._replace_day("rimili", later_day, [("1001", "RK-1", "Товар 1", 3, 450)])
        funnel_orders._replace_day("tris", first_day, [("1002", "TR-1", "Товар 2", 2, 300)])

        rows = db.get_unit_economics_1c_product_sales_starts(("rimili",))

        self.assertEqual(
            rows,
            [{"store_slug": "rimili", "article": "1001", "first_sale_at": later_day.isoformat()}],
        )

    def test_funnel_order_totals_use_requested_period_and_stores(self) -> None:
        first_day = date.today() - timedelta(days=2)
        second_day = date.today() - timedelta(days=1)
        outside_period = date.today()
        funnel_orders._replace_day(
            "rimili", first_day, [("1001", "RK-1", "Товар 1", 4, 500, 1, 125, 3, 300, 75)]
        )
        funnel_orders._replace_day(
            "rimili", second_day, [("1001", "RK-1", "Товар 1", 6, 900, 2, 300, 4, 600, 80)]
        )
        funnel_orders._replace_day(
            "rimili",
            outside_period,
            [("1001", "RK-1", "Товар 1", 8, 1_200)],
        )
        funnel_orders._replace_day("tris", first_day, [("1002", "TR-1", "Товар 2", 3, 450)])

        rows = db.get_unit_economics_1c_funnel_order_totals(
            ("rimili",),
            first_day.isoformat(),
            second_day.isoformat(),
        )

        self.assertEqual(
            rows,
            [
                {
                    "store_slug": "rimili",
                    "article": "1001",
                    "orders_count": 10,
                    "orders_amount": 1_400.0,
                    "cancel_count": 3,
                    "cancel_amount": 425.0,
                    "buyout_count": 7,
                    "buyout_amount": 900.0,
                    "buyout_percent": 78.0,
                    "net_orders_count": 7,
                    "net_orders_amount": 975.0,
                }
            ],
        )

    def test_weekly_metrics_sync_persists_buyout_percent(self) -> None:
        with (
            mock.patch.object(funnel_orders.wb_tokens, "has_token", return_value=True),
            mock.patch.object(funnel_orders.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(
                funnel_orders.wb_api,
                "request",
                return_value=_response(
                    article="1001",
                    orders=25,
                    amount=12_500,
                    cancellations=4,
                    cancellation_amount=2_000,
                    buyout_percent=73.45,
                ),
            ) as request,
        ):
            result = funnel_orders.sync_weekly_metrics_store("rimili")

        self.assertEqual(result, {"store": "rimili", "status": "success", "records": 1})
        rows = db.get_unit_economics_1c_funnel_product_metrics(("rimili",))
        self.assertEqual(rows[0]["article"], "1001")
        self.assertEqual(rows[0]["orders_count"], 25)
        self.assertEqual(rows[0]["orders_amount"], 12_500)
        self.assertEqual(rows[0]["cancel_count"], 4)
        self.assertEqual(rows[0]["cancel_amount"], 2_000)
        self.assertEqual(rows[0]["buyout_percent"], 73.45)
        body = request.call_args.kwargs["json_body"]
        self.assertEqual(
            (date.fromisoformat(body["selectedPeriod"]["end"]) - date.fromisoformat(body["selectedPeriod"]["start"])).days,
            13,
        )
        self.assertEqual(
            date.fromisoformat(body["selectedPeriod"]["end"]),
            datetime.now(funnel_orders.MOSCOW).date() - timedelta(days=1),
        )

    def test_buyout_metrics_use_each_cabinet_period(self) -> None:
        db.save_unit_economics_1c_cabinet_settings(
            "rimili",
            UnitEconomics1CCabinetSettingsRequest(buyout_period_days=21),
            updated_at="2026-09-02T10:00:00+00:00",
            updated_by_user_id=1,
            updated_by_name="Test",
        )
        with (
            mock.patch.object(funnel_orders.wb_tokens, "has_token", return_value=True),
            mock.patch.object(funnel_orders.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(funnel_orders.wb_api, "request", return_value={"data": {}}) as request,
        ):
            funnel_orders.sync_weekly_metrics_store("rimili")

        body = request.call_args.kwargs["json_body"]
        selected = body["selectedPeriod"]
        self.assertEqual(
            (date.fromisoformat(selected["end"]) - date.fromisoformat(selected["start"])).days,
            20,
        )

    def test_sync_window_does_not_trigger_an_unbounded_legacy_backfill(self) -> None:
        legacy_day = date.today() - timedelta(days=100)
        current_day = date.today() - timedelta(days=1)
        funnel_orders._replace_day(
            "rimili",
            current_day,
            [("1001", "RK-1", "Товар", 5, 500, 1, 100)],
        )
        conn = db.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO wb_funnel_daily_orders
                    (store_slug, day, article, vendor_code, product_name,
                     orders_count, orders_amount, cancel_count, cancel_amount,
                     source_version, updated_at)
                VALUES ('rimili', ?, '1001', 'RK-1', 'Товар', 4, 400, 0, 0, 2, 'legacy')
                """,
                (legacy_day.isoformat(),),
            )
            conn.execute(
                """
                INSERT INTO wb_funnel_product_metrics
                    (store_slug, article, period_from, period_to, orders_count, orders_amount,
                     cancel_count, cancel_amount, buyout_percent, source_version, updated_at)
                VALUES ('rimili', 'legacy', ?, ?, 4, 400, 0, 0, 50, 1, 'legacy')
                """,
                (legacy_day.isoformat(), legacy_day.isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

        db.init_db()

        days_to_sync = funnel_orders._days_to_sync("rimili")
        self.assertEqual(
            days_to_sync,
            [
                date.today() - timedelta(days=offset)
                for offset in range(funnel_orders.RECENT_REFRESH_DAYS)
            ],
        )
        self.assertNotIn(legacy_day, days_to_sync)
        totals = db.get_unit_economics_1c_funnel_order_totals(
            ("rimili",), legacy_day.isoformat(), current_day.isoformat()
        )
        self.assertEqual(totals[0]["orders_count"], 5)
        self.assertEqual(totals[0]["cancel_count"], 1)
        self.assertFalse(
            any(row["article"] == "legacy" for row in db.get_unit_economics_1c_funnel_product_metrics(("rimili",)))
        )
        conn = db.get_connection()
        try:
            source_version = conn.execute(
                "SELECT source_version FROM wb_funnel_daily_orders WHERE day=?",
                (legacy_day.isoformat(),),
            ).fetchone()["source_version"]
        finally:
            conn.close()
        self.assertEqual(source_version, 2)


if __name__ == "__main__":
    unittest.main()
