import json
import logging
import unittest
from datetime import UTC, date, datetime, timedelta
from unittest import mock

from pydantic import ValidationError

from app import rnp
from app.application.rnp_commands import RnpCommandService
from app.dto.identity import Role, User
from app.dto.marketplace import Marketplace
from app.dto.rnp import RnpAction, RnpActionRequest, RnpStrategy, RnpStrategyRequest


class RnpServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)

    def test_period_marketplace_and_lookback_validation(self) -> None:
        start, end = rnp._parse_month("2026-08")
        self.assertEqual(start, date(2026, 8, 1))
        self.assertEqual(end, date(2026, 9, 1))
        for value in ("bad", "2099-01", "2000-01"):
            with self.assertRaises(ValueError):
                rnp._parse_month(value)
        self.assertEqual(rnp._normalize_marketplace("wb"), "WB")
        with self.assertRaises(ValueError):
            rnp._normalize_marketplace("wrong")
        self.assertGreater(rnp.sales_lookback_days("2026-01", "WB", date(2026, 8, 1)), 30)
        self.assertGreater(rnp.sales_lookback_days("bad", "OZON"), 0)
        self.assertEqual(rnp._number("2.5"), 2.5)
        self.assertEqual(rnp._integer("bad", 4), 4)

    def test_metric_derivation_api_and_normalization(self) -> None:
        item = rnp._empty_day()
        item.update(
            {
                "orders_amount": 1000,
                "orders_count": 10,
                "sales_amount": 800,
                "sales_count": 8,
                "gross_profit": 400,
                "traffic_clicks": 100,
                "traffic_carts": 30,
                "ad_impressions": 1000,
                "ad_clicks": 20,
                "ad_spend": 100,
                "plan_orders_amount": 2000,
                "unified_impressions": 500,
                "unified_clicks": 10,
                "unified_spend": 50,
                "unified_orders": 3,
                "unified_carts": 5,
            }
        )
        derived = rnp._derive_metrics(item)
        self.assertEqual(derived["traffic_orders"], 10)
        self.assertEqual(derived["buyout_count"], 8)
        self.assertEqual(derived["traffic_organic"], 80)
        self.assertEqual(derived["ad_ctr"], 2)
        self.assertEqual(derived["profit_after_ads"], 300)
        self.assertEqual(derived["plan_completion"], 50)
        self.assertIsNone(rnp._ratio(1, 0))

        api_rows = [
            {
                "article": "A",
                "day": "2026-08-01",
                "traffic_clicks": 10,
                "ad_spend": 20,
                "rating": 4,
                "stock_regions": json.dumps({"North": 2}),
            },
            {
                "article": "B",
                "day": "2026-08-01",
                "traffic_clicks": 5,
                "ad_spend": 10,
                "rating": 5,
                "stock_regions": json.dumps({"North": 1, "South": 3}),
            },
        ]
        aggregated = rnp._api_daily(api_rows)["2026-08-01"]
        self.assertEqual(aggregated["traffic_clicks"], 15)
        self.assertEqual(aggregated["rating"], 4.5)
        self.assertIn("South", aggregated["stock_regions"])
        by_article = rnp._api_daily(api_rows, article_key=True)
        self.assertEqual(by_article[("A", "2026-08-01")]["rating"], 4)

        normalized = rnp._normalize_daily(
            [
                {
                    "article": "A",
                    "day": "2026-08-01",
                    "orders_amount": 100,
                    "orders_count": 2,
                    "sales_amount": 80,
                    "sales_count": 1,
                    "gross_profit": 30,
                    "costed_sales_count": 1,
                    "return_count": 1,
                    "return_amount": 5,
                }
            ],
            article_key=True,
        )
        row = normalized[("A", "2026-08-01")]
        self.assertEqual(row["average_order"], 50)
        self.assertEqual(row["gross_profit"], 30)
        merged = rnp._merge_day(row, {"traffic_clicks": 10, "ad_spend": None})
        self.assertEqual(merged["traffic_clicks"], 10)

    def test_summary_fact_and_forecast(self) -> None:
        daily = {
            "2026-08-01": rnp._derive_metrics(
                {
                    **rnp._empty_day(),
                    "orders_amount": 100,
                    "orders_count": 2,
                    "sales_amount": 80,
                    "sales_count": 1,
                    "gross_profit": 30,
                    "costed_sales_count": 1,
                    "traffic_clicks": 10,
                    "stock_units": 5,
                }
            )
        }
        fact, forecast = rnp._summary(daily, 31, 10, 7)
        self.assertEqual(fact["orders_amount"], 100)
        self.assertEqual(fact["stock_units"], 5)
        self.assertEqual(forecast["orders_amount"], 310)
        full_fact, full_forecast = rnp._summary(daily, 10, 10, 7)
        self.assertEqual(full_fact, full_forecast)

    def test_sync_state_variants(self) -> None:
        with mock.patch.object(rnp.db, "get_sales_sync_states", return_value=[]):
            self.assertEqual(rnp._sync_state("store", "WB")["status"], "waiting")
        with mock.patch.object(
            rnp.db,
            "get_sales_sync_states",
            return_value=[{"ok": 1, "last_success_at": "now"}],
        ):
            self.assertEqual(rnp._sync_state("store", "WB")["status"], "ready")
        with mock.patch.object(
            rnp.db,
            "get_sales_sync_states",
            return_value=[{"ok": 0, "error": "403 forbidden"}],
        ):
            state = rnp._sync_state("store", "YANDEX MARKET")
        self.assertEqual(state["status"], "warning")
        self.assertNotEqual(state["error"], "403 forbidden")
        with mock.patch.object(
            rnp.db,
            "get_sales_sync_states",
            return_value=[{"ok": 0, "error": "boom"}],
        ):
            self.assertIn("boom", rnp._sync_state("store", "WB")["error"])

    def test_metric_sync_state_variants(self) -> None:
        states = [
            {"source": "snapshot", "status": "success", "rows_received": 2, "last_success_at": "now"},
            {"source": "funnel", "status": "success", "rows_received": 3, "last_success_at": "now"},
        ]
        with mock.patch.object(rnp.rnp_analytics, "get_states", return_value=states):
            ozon = rnp._metric_sync_state("store", "OZON")
        self.assertEqual(next(row for row in ozon if row["source"] == "funnel")["status"], "partial")
        self.assertEqual(next(row for row in ozon if row["source"] == "advertising")["status"], "unavailable")
        with mock.patch.object(
            rnp.rnp_analytics,
            "get_states",
            return_value=[{"source": "funnel", "status": "error", "error": "403", "rows_received": 0}],
        ):
            yandex = rnp._metric_sync_state("store", "YANDEX MARKET")
        self.assertNotEqual(next(row for row in yandex if row["source"] == "funnel")["message"], "403")
        with mock.patch.object(rnp.rnp_analytics, "get_states", return_value=[]):
            wb = rnp._metric_sync_state("store", "WB")
        self.assertTrue(all(row["status"] == "waiting" for row in wb))

    def test_dashboard_full_product_chain(self) -> None:
        month = "2026-08"
        catalog = {
            "total": 1,
            "items": [
                {
                    "article": "A",
                    "barcode": "bc",
                    "name": "Product",
                    "mp_sku": "sku",
                    "mp_product_id": "pid",
                    "image_url": "image",
                    "current_stock": 10,
                    "stock_updated_at": "now",
                    "buyer_price": 900,
                    "discounted_price": 950,
                    "list_price": 1000,
                    "spp_percent": 10,
                    "purchase_price": 400,
                }
            ],
        }
        product_daily = [
            {
                "article": "A",
                "day": "2026-08-01",
                "orders_amount": 1000,
                "orders_count": 2,
                "sales_amount": 900,
                "sales_count": 1,
                "cancellations_amount": 0,
                "cancellations_count": 0,
                "gross_profit": 400,
                "costed_sales_count": 1,
            }
        ]
        api_daily = [
            {
                "article": "A",
                "day": "2026-08-01",
                "traffic_clicks": 100,
                "traffic_carts": 20,
                "traffic_orders": 2,
                "ad_spend": 100,
                "ad_impressions": 1000,
                "ad_clicks": 20,
                "stock_units": 10,
                "rating": 4.8,
            }
        ]
        with (
            mock.patch.object(rnp, "STORES", {"store": {"name": "Store"}}),
            mock.patch.object(rnp.db, "get_rnp_catalog_page", return_value=catalog),
            mock.patch.object(rnp.db, "get_rnp_product_daily", return_value=product_daily),
            mock.patch.object(rnp.db, "get_rnp_daily_totals", return_value=product_daily),
            mock.patch.object(rnp.rnp_analytics, "get_daily", side_effect=[api_daily, api_daily]),
            mock.patch.object(rnp.db, "get_rnp_strategies", return_value={"A": {"strategy": "growth"}}),
            mock.patch.object(
                rnp.db,
                "get_rnp_action_logs",
                return_value=[{"article": "A", "action_date": "2026-08-01", "note": "Done"}],
            ),
            mock.patch.object(rnp.db, "get_rnp_stock_total", return_value=10),
            mock.patch.object(rnp, "_sync_state", return_value={"status": "ready"}),
            mock.patch.object(rnp, "_metric_sync_state", return_value=[]),
        ):
            result = rnp.dashboard(month, "WB", "store", limit=100, offset=-1)
        self.assertEqual(result["products"][0]["article"], "A")
        self.assertEqual(result["products"][0]["current_price"], 900)
        self.assertEqual(result["products"][0]["fact"]["orders_amount"], 1000)
        self.assertEqual(result["pagination"]["limit"], 50)
        self.assertEqual(result["totals"]["current_stock"], 10)

        with mock.patch.object(rnp, "STORES", {"store": {}}):
            with self.assertRaises(ValueError):
                rnp.dashboard(month, "WB", "wrong")

    def test_sync_metrics_and_mutations(self) -> None:
        with (
            mock.patch.object(rnp, "STORES", {"store": {}}),
            mock.patch.object(rnp.rnp_analytics, "sync_store", return_value={"ok": True}) as sync,
        ):
            result = rnp.sync_metrics("2026-08", "WB", "store", articles=[" A ", ""])
        self.assertTrue(result["ok"])
        self.assertEqual(sync.call_args.args[-1], ["A"])
        with mock.patch.object(rnp, "STORES", {"store": {}}):
            with self.assertRaises(ValueError):
                rnp.sync_metrics("2026-08", "WB", "wrong")

        now = datetime(2026, 8, 12, 10, tzinfo=UTC)
        repository = mock.Mock()
        repository.article_exists.return_value.root = True
        unit_of_work = mock.MagicMock()
        unit_of_work.__enter__.return_value = unit_of_work
        unit_of_work.repository = repository
        commands = RnpCommandService(lambda: unit_of_work, clock=lambda: now)
        actor = User(
            id=1,
            full_name="User",
            login="user",
            role=Role.ADMIN,
            created_at=now,
        )
        strategy_request = RnpStrategyRequest(
            store="store",
            marketplace=Marketplace.WB,
            article="A",
            strategy="growth",
            date_from="2026-08-01",
            date_to="2026-08-31",
        )
        repository.save_strategy.return_value = RnpStrategy(
            store_slug="store",
            marketplace=Marketplace.WB,
            article="A",
            strategy="growth",
            date_from="2026-08-01",
            date_to="2026-08-31",
            updated_by="User",
            updated_at=now,
        )
        self.assertEqual(commands.save_strategy(strategy_request, actor).strategy, "growth")

        action_request = RnpActionRequest(
            store="store",
            marketplace=Marketplace.WB,
            article="A",
            note="Done",
            action_date=now.date(),
        )
        repository.add_action.return_value = RnpAction(
            id=1,
            article="A",
            note="Done",
            action_date=now.date(),
            user_name="User",
            created_at=now,
        )
        self.assertEqual(commands.add_action(action_request, actor).note, "Done")
        unit_of_work.commit.assert_called()

        with self.assertRaises(ValidationError):
            RnpStrategyRequest.model_validate({**strategy_request.model_dump(), "date_from": "2026-09-01"})
        with self.assertRaises(ValueError):
            commands.add_action(
                action_request.model_copy(update={"action_date": now.date() + timedelta(days=1)}),
                actor,
            )


if __name__ == "__main__":
    unittest.main()
