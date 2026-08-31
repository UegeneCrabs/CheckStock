import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from app import db, unit_economics_1c
from app import unit_economics_1c_advertising as advertising
from app.domain import MOSCOW_TIMEZONE
from app.repositories import core
from app.wb import funnel_orders
from app.web import templating

NOW = "2026-08-19T08:00:00+00:00"


def order_line(
    order_key: str,
    nm_id: str,
    amount: float,
    *,
    cancelled: bool = False,
    sold: bool = False,
    retail_price: float | None = None,
) -> dict:
    raw = {"nmId": int(nm_id), "isCancel": cancelled}
    if retail_price is not None:
        raw["priceWithDisc"] = retail_price
    return {
        "store_slug": "rimili",
        "marketplace": "WB",
        "order_key": order_key,
        "line_key": order_key,
        "external_order_id": order_key,
        "scheme": "fbo",
        "status": "cancelled" if cancelled else "ordered",
        "substatus": "",
        "article": f"vendor-{nm_id}",
        "barcode": order_key,
        "name": "Товар",
        "ordered_at": "2026-08-18T10:00:00+03:00",
        "source_updated_at": NOW,
        "cancelled_at": NOW if cancelled else None,
        "sold_at": NOW if sold else None,
        "returned_at": None,
        "quantity": 1,
        "cancelled_quantity": 1 if cancelled else 0,
        "sold_quantity": 1 if sold else 0,
        "return_quantity": 0,
        "order_amount": amount,
        "cancelled_amount": amount if cancelled else 0,
        "sale_amount": 0,
        "return_amount": 0,
        "currency": "RUB",
        "raw_json": json.dumps(raw),
    }


class UnitEconomics1CAdvertisingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = mock.patch.object(core, "DB_PATH", Path(self.temp.name) / "ads.sqlite3")
        self.path_patch.start()
        db.init_db()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temp.cleanup()

    def test_sync_saves_daily_spend_for_every_supported_campaign(self) -> None:
        campaigns = {
            "adverts": [
                {
                    "status": 9,
                    "advert_list": [{"advertId": 10}, {"advertId": 20}],
                },
                {
                    "status": 7,
                    "advert_list": [{"advertId": 25, "changeTime": "2026-08-01T10:00:00+03:00"}],
                },
                {"status": 8, "advert_list": [{"advertId": 30}]},
            ]
        }
        stats = [
            {
                "advertId": 10,
                "days": [
                    {
                        "date": "2026-08-18T00:00:00Z",
                        "apps": [
                            {
                                "nm": [
                                    {
                                        "nmId": 123,
                                        "sum": 100.5,
                                        "views": 1000,
                                        "clicks": 20,
                                    }
                                ]
                            }
                        ],
                    }
                ],
            },
            {
                "advertId": 20,
                "days": [
                    {
                        "date": "2026-08-18T00:00:00Z",
                        "apps": [
                            {
                                "nms": [
                                    {
                                        "nmId": 123,
                                        "spend": 49.5,
                                        "impressions": 500,
                                        "clicks": 10,
                                    }
                                ]
                            }
                        ],
                    },
                    {
                        "date": "2026-08-19T00:00:00Z",
                        "apps": [
                            {
                                "nm": [
                                    {
                                        "nmId": 999,
                                        "sum": 25,
                                        "views": 200,
                                        "clicks": 5,
                                    }
                                ]
                            }
                        ],
                    },
                ],
            },
        ]

        def request(_method, url, _token, params=None):
            self.assertTrue(params is None or len(str(params["ids"]).split(",")) <= 50)
            return campaigns if url == advertising.CAMPAIGNS_URL else stats

        with (
            mock.patch.object(advertising.wb_tokens, "has_token", return_value=True),
            mock.patch.object(advertising.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(advertising.wb_api, "request", side_effect=request),
        ):
            report = advertising.sync_store("rimili", date(2026, 8, 19))

        self.assertTrue(report["ok"])
        rows = db.get_unit_economics_1c_daily_advertising(("rimili",), "2026-08-13", "2026-08-19")
        self.assertEqual(
            [(row["nm_id"], row["day"], row["spend"]) for row in rows],
            [
                ("123", "2026-08-18", 150.0),
                ("999", "2026-08-19", 25.0),
            ],
        )
        first = next(row for row in rows if row["nm_id"] == "123")
        self.assertEqual((first["impressions"], first["clicks"]), (1500, 30))
        metrics = unit_economics_1c.load_product_metrics(
            ("rimili",),
            today=date(2026, 8, 19),
        )[("rimili", "123")]
        self.assertEqual(metrics["average_daily_spend"], round(150 / 7, 2))
        self.assertEqual(metrics["ctr"], 2)
        self.assertEqual(metrics["cpc"], 5)
        state = db.list_unit_economics_1c_advertising_sync_states(("rimili",))[0]
        self.assertEqual((state["status"], state["campaigns_count"]), ("ok", 2))

    def test_real_drr_uses_order_amount_and_zero_order_rule(self) -> None:
        db.replace_unit_economics_1c_daily_advertising(
            "rimili",
            "2026-08-13",
            "2026-08-19",
            [
                {
                    "store_slug": "rimili",
                    "nm_id": "123",
                    "day": "2026-08-18",
                    "marketplace": "WB",
                    "spend": 150,
                    "synced_at": NOW,
                },
                {
                    "store_slug": "rimili",
                    "nm_id": "999",
                    "day": "2026-08-19",
                    "marketplace": "WB",
                    "spend": 25,
                    "synced_at": NOW,
                },
            ],
        )
        funnel_orders._replace_day(
            "rimili",
            date(2026, 8, 18),
            [("123", "V-123", "Товар", 1, 1000)],
        )
        funnel_orders._replace_product_metrics(
            "rimili",
            date(2026, 8, 13),
            date(2026, 8, 19),
            [("123", 1, 1000, 75)],
        )

        metrics = unit_economics_1c.load_product_metrics(("rimili",), today=date(2026, 8, 19))

        self.assertEqual(metrics[("rimili", "123")]["orders_amount"], 1000)
        self.assertEqual(metrics[("rimili", "123")]["drr"], 20)
        self.assertEqual(metrics[("rimili", "999")]["orders_amount"], 0)
        self.assertEqual(metrics[("rimili", "999")]["drr"], 100)
        daily_123 = {item["date"]: item for item in metrics[("rimili", "123")]["daily"]}
        daily_999 = {item["date"]: item for item in metrics[("rimili", "999")]["daily"]}
        self.assertEqual(daily_123["2026-08-18"]["drr"], 20)
        self.assertEqual(daily_999["2026-08-19"]["drr"], 100)
        self.assertEqual(daily_123["2026-08-19"]["drr"], 0)
        self.assertEqual(unit_economics_1c.calculate_drr_percent(0, 0), 0)
        self.assertEqual(unit_economics_1c.calculate_drr_percent(12_336.11, 172_511), 7.15)
        self.assertEqual(unit_economics_1c.calculate_drr_percent(12_336.11, 172_511, 80), 8.94)
        self.assertTrue(
            unit_economics_1c._attempted_today(
                {"last_attempt_at": "2026-08-18T22:30:00+00:00"},
                date(2026, 8, 19),
            )
        )
        self.assertFalse(unit_economics_1c._attempted_today(None, date(2026, 8, 19)))

    def test_product_metrics_include_persisted_funnel_buyout_percent(self) -> None:
        funnel_orders._replace_day(
            "rimili",
            date(2026, 8, 18),
            [("123", "V-123", "Товар", 3, 3000)],
        )
        funnel_orders._replace_product_metrics(
            "rimili",
            date(2026, 8, 13),
            date(2026, 8, 19),
            [("123", 3, 3000, 66.67)],
        )

        metrics = unit_economics_1c.load_product_metrics(("rimili",), today=date(2026, 8, 19))[
            ("rimili", "123")
        ]

        self.assertEqual(metrics["orders_count"], 3)
        self.assertEqual(metrics["buyout_percent"], 66.67)
        self.assertEqual(metrics["buyout_orders_count"], 3)

    def test_product_metrics_use_matching_weekly_funnel_when_daily_rows_are_legacy(self) -> None:
        conn = db.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO wb_funnel_daily_orders
                    (store_slug, day, article, vendor_code, product_name,
                     orders_count, orders_amount, cancel_count, cancel_amount,
                     source_version, updated_at)
                VALUES ('rimili', '2026-08-19', '123', 'V-123', 'Товар',
                        26, 93444, 0, 0, 2, ?)
                """,
                (NOW,),
            )
            conn.commit()
        finally:
            conn.close()
        funnel_orders._replace_product_metrics(
            "rimili",
            date(2026, 8, 13),
            date(2026, 8, 19),
            [("123", 314, 1_128_515, 11, 39_534, 82)],
        )

        metrics = unit_economics_1c.load_product_metrics(
            ("rimili",),
            today=date(2026, 8, 19),
        )[("rimili", "123")]

        self.assertEqual(metrics["orders_count"], 314)
        self.assertEqual(metrics["orders_amount"], 1_128_515)
        self.assertEqual(metrics["cancel_count"], 11)
        self.assertEqual(metrics["cancel_amount"], 39_534)
        self.assertEqual(metrics["net_orders_count"], 303)
        self.assertEqual(metrics["net_orders_amount"], 1_088_981)

    def test_three_week_order_demand_is_loaded_from_funnel(self) -> None:
        funnel_orders._replace_day(
            "rimili",
            date(2026, 8, 18),
            [("123", "V-123", "Товар", 2, 2000)],
        )

        demand = unit_economics_1c.load_product_average_daily_orders(
            ("rimili",),
            today=date(2026, 8, 19),
        )[("rimili", "123")]
        metrics = unit_economics_1c.load_product_metrics(
            ("rimili",),
            today=date(2026, 8, 19),
        )[("rimili", "123")]

        self.assertEqual(demand["period_days"], 21)
        self.assertEqual(demand["orders_count"], 2)
        self.assertEqual(demand["average_daily_orders"], round(2 / 21, 4))
        self.assertEqual(metrics["orders_amount"], 2000)
        self.assertEqual(unit_economics_1c.calculate_stock_coverage_days(210, 42), 105)
        self.assertEqual(unit_economics_1c.calculate_stock_coverage_days(210, 0), 0)
        self.assertEqual(unit_economics_1c.calculate_stock_coverage_days(0, 42), 0)

    def test_scheduled_due_sync_refreshes_only_prices(self) -> None:
        with (
            mock.patch.object(
                unit_economics_1c.db,
                "list_unit_economics_1c_price_sync_states",
                return_value=[],
            ),
            mock.patch.object(
                unit_economics_1c.price_sync,
                "sync_stores",
                return_value={},
            ) as price_sync,
            mock.patch.object(unit_economics_1c.advertising_sync, "sync_stores") as advertising_sync,
        ):
            unit_economics_1c.sync_prices_due()

        price_sync.assert_called_once()
        advertising_sync.assert_not_called()

    def test_wallet_sync_skips_seller_prices_and_does_not_change_full_sync_state(self) -> None:
        with mock.patch.object(
            unit_economics_1c.price_sync,
            "sync_stores",
            return_value={},
        ) as price_sync:
            unit_economics_1c.sync_wallet_prices()

        price_sync.assert_called_once_with(
            tuple(unit_economics_1c.STORES),
            load_retail_prices=False,
            record_state=False,
        )

    def test_price_sync_skips_cabinets_refreshed_within_four_hours(self) -> None:
        stores = tuple(unit_economics_1c.STORES)
        recent_store, stale_store = stores[:2]
        now = datetime.now(MOSCOW_TIMEZONE)
        states = [
            {
                "store_slug": recent_store,
                "last_attempt_at": (now - timedelta(hours=1)).isoformat(),
            },
            {
                "store_slug": stale_store,
                "last_attempt_at": (now - timedelta(hours=5)).isoformat(),
            },
        ]
        with (
            mock.patch.object(
                unit_economics_1c.db,
                "list_unit_economics_1c_price_sync_states",
                return_value=states,
            ),
            mock.patch.object(
                unit_economics_1c.price_sync,
                "sync_stores",
                return_value={},
            ) as price_sync,
        ):
            unit_economics_1c.sync_prices_due()

        due_stores = price_sync.call_args.args[0]
        self.assertNotIn(recent_store, due_stores)
        self.assertIn(stale_store, due_stores)

    def test_api_access_error_is_rendered_in_rotating_banner(self) -> None:
        db.record_unit_economics_1c_advertising_sync_state(
            "tris",
            status="error",
            date_from="2026-08-13",
            date_to="2026-08-19",
            attempted_at=NOW,
            rows_saved=0,
            campaigns_count=0,
            error="нет доступа — проверьте категории у токена",
        )
        user = {"store_slugs": ["tris"], "role": "admin", "full_name": "Test User"}
        with mock.patch.object(templating.token_watch, "get_warnings", return_value=[]):
            banner = templating.render_system_alerts(
                user,
                [{"title": "Другое предупреждение", "text": "Проверка"}],
            )

        self.assertIn("Кабинет TRIS", banner)
        self.assertIn("проверьте права API-ключа WB", banner)
        self.assertIn("Продвижение", banner)
        self.assertEqual(banner.count("data-system-alert-item"), 2)
        self.assertIn("data-system-alerts", banner)

    def test_transient_advertising_error_is_not_rendered_in_banner(self) -> None:
        db.record_unit_economics_1c_advertising_sync_state(
            "tris",
            status="error",
            date_from="2026-08-13",
            date_to="2026-08-19",
            attempted_at=NOW,
            rows_saved=0,
            campaigns_count=0,
            error="WB 429: rate limit exceeded",
        )
        user = {"store_slugs": ["tris"], "role": "admin", "full_name": "Test User"}
        with mock.patch.object(templating.token_watch, "get_warnings", return_value=[]):
            banner = templating.render_system_alerts(user)

        self.assertEqual(banner, "")


if __name__ == "__main__":
    unittest.main()
