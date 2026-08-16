import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from app import db
from app.repositories import core
from app.wb import funnel_orders


def _response(
    *,
    article: str = "1001",
    vendor_code: str = "RK-1001",
    name: str = "Тестовый товар",
    orders: int,
    amount: float,
    cancellations: int,
    cancellation_amount: float,
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

    def test_sync_store_persists_daily_net_amount_and_count(self) -> None:
        first_day = date.today() - timedelta(days=1)
        second_day = date.today()
        with (
            mock.patch.object(funnel_orders, "_days_to_sync", return_value=[first_day, second_day]),
            mock.patch.object(funnel_orders.wb_tokens, "has_token", return_value=True),
            mock.patch.object(funnel_orders.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(funnel_orders, "REQUEST_PAUSE_SECONDS", 0),
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
                {"date": first_day.isoformat(), "orders_count": 8, "orders_amount": 1_200.0},
                {"date": second_day.isoformat(), "orders_count": 6, "orders_amount": 600.0},
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
            article="1001", orders=5, amount=500, cancellations=1, cancellation_amount=100, cursor="next-page"
        )
        second_page = _response(article="1002", orders=3, amount=300, cancellations=0, cancellation_amount=0)
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
                "SELECT article, vendor_code, product_name, orders_count, orders_amount "
                "FROM wb_funnel_daily_orders ORDER BY article"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(
            [tuple(row.values()) for row in rows],
            [
                ("1001", "RK-1001", "Тестовый товар", 4, 400.0),
                ("1002", "RK-1001", "Тестовый товар", 3, 300.0),
            ],
        )

    def test_dashboard_aggregates_all_stores(self) -> None:
        day = date.today()
        funnel_orders._replace_day("rimili", day, [("1001", "RK-1", "Товар 1", 4, 500)])
        funnel_orders._replace_day("tris", day, [("1002", "TR-1", "Товар 2", 6, 900)])

        payload = funnel_orders.dashboard(day.isoformat(), day.isoformat())

        self.assertEqual(payload["store"], "all")
        self.assertEqual(
            payload["series"], [{"date": day.isoformat(), "orders_count": 10, "orders_amount": 1_400.0}]
        )


if __name__ == "__main__":
    unittest.main()
