import logging
import math
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from app import db, decision_center
from app.container import ApplicationContainer
from app.dto.decision import DecisionStatus, DecisionStatusRequest
from app.dto.identity import Role, User
from app.repositories import core


class DecisionCenterTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.sqlite3"
        self.path_patch = mock.patch.object(core, "DB_PATH", self.db_path)
        self.path_patch.start()
        db.init_db()
        decision_center.init_schema()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temp.cleanup()
        logging.disable(logging.NOTSET)

    def test_helpers_sync_state_and_action_status(self) -> None:
        self.assertEqual(decision_center._number("2.5"), 2.5)
        self.assertEqual(decision_center._number(float("inf"), 7), 7)
        self.assertEqual(decision_center._integer("bad", 3), 3)
        self.assertEqual(decision_center._mapping([]), {})
        self.assertEqual(decision_center._items({}), [])
        self.assertEqual(decision_center._clamp(20, 0, 10), 10)
        self.assertEqual(decision_center._base_nm("123 / S"), 123)
        self.assertEqual(decision_center._base_nm("bad", 9), 9)
        self.assertEqual(decision_center._median([0, math.nan, 2, 4], 1), 3)
        self.assertEqual(decision_center._median([], 7), 7)

        attempted = datetime.now(UTC).isoformat()
        decision_center._record_sync("store", "funnel", "running", attempted)
        state = decision_center._sync_state("store", "funnel")
        self.assertEqual(state["status"], "running")
        decision_center._record_sync("store", "funnel", "success", attempted, 3, None, 20)
        self.assertEqual(decision_center._sync_state("store", "funnel")["records"], 3)
        self.assertTrue(decision_center._is_due("store", "search", False))
        self.assertTrue(decision_center._is_due("store", "funnel", True))
        self.assertFalse(decision_center._is_due("store", "funnel", False))

        user = User(
            id=1,
            full_name="User",
            login="user",
            role=Role.ADMIN,
            created_at=attempted,
        )
        result = ApplicationContainer().decision_commands.set_status(
            DecisionStatusRequest(fingerprint="store:1:stockout", status=DecisionStatus.IN_PROGRESS),
            user,
        )
        self.assertEqual(result.status, DecisionStatus.IN_PROGRESS)
        self.assertEqual(decision_center._action_states()["store:1:stockout"], "in_progress")

    def test_upsert_and_source_loaders(self) -> None:
        funnel_response = {
            "data": {
                "products": [
                    {
                        "product": {"nmId": 10, "title": "Product", "feedbackRating": 4.7},
                        "statistic": {
                            "selected": {
                                "openCount": 100,
                                "cartCount": 20,
                                "orderCount": 10,
                                "buyoutCount": 7,
                                "cancelCount": 1,
                                "orderSum": 5000,
                                "timeToReady": {"days": 2, "hours": 12},
                            },
                            "comparison": {"orderCountDynamic": 25},
                        },
                    }
                ]
            }
        }
        with mock.patch.object(decision_center, "_request", return_value=funnel_response):
            self.assertEqual(decision_center._sync_funnel("store", "token"), 1)

        search_response = {
            "data": {
                "groups": [
                    {
                        "items": [
                            {
                                "nmId": 10,
                                "avgPosition": {"current": 40},
                                "openCard": {"current": 90},
                                "addToCart": {"current": 20},
                                "orders": {"current": 8, "dynamics": 10},
                                "visibility": {"current": 140},
                            }
                        ]
                    }
                ]
            }
        }
        with mock.patch.object(decision_center, "_request", return_value=search_response):
            self.assertEqual(decision_center._sync_search("store", "token"), 1)

        campaign_response = {
            "adverts": [
                {"status": 9, "advert_list": [{"advertId": 1, "changeTime": "2026"}]},
                {"status": 1, "advert_list": [{"advertId": 2}]},
            ]
        }
        stats_response = [
            {
                "days": [
                    {
                        "apps": [
                            {
                                "nms": [
                                    {
                                        "nmId": 10,
                                        "views": 1000,
                                        "clicks": 10,
                                        "sum": 500,
                                        "orders": 2,
                                        "avg_position": 30,
                                    },
                                    {"nmId": 0, "views": 5},
                                ]
                            }
                        ]
                    }
                ]
            }
        ]
        with mock.patch.object(decision_center, "_request", side_effect=[campaign_response, stats_response]):
            self.assertEqual(decision_center._sync_advertising("store", "token"), 1)

        root_format = {
            "items": [
                {
                    "nmId": 11,
                    "dailyStats": [
                        {"stat": {"views": 10, "clicks": 2, "spend": 3, "orders": 1, "avgPos": 4}}
                    ],
                }
            ]
        }
        self.assertEqual(decision_center._flatten_advertising(root_format)[0]["nm_id"], 11)
        self.assertEqual(decision_center._upsert_rows("store", [], ("views",), "funnel_synced_at", "now"), 0)

        with mock.patch.object(decision_center, "_request", return_value={"adverts": []}):
            self.assertEqual(decision_center._sync_advertising("store", "token"), 0)

    def test_sync_orchestration_all_paths(self) -> None:
        with mock.patch.object(decision_center, "STORES", {"store": {}}):
            with self.assertRaises(ValueError):
                decision_center.sync_store("unknown")
            with mock.patch.object(decision_center.wb_tokens, "has_token", return_value=False):
                self.assertFalse(decision_center.sync_store("store")["configured"])

            with (
                mock.patch.object(decision_center.wb_tokens, "has_token", return_value=True),
                mock.patch.object(decision_center.wb_tokens, "get_token", return_value="token"),
                mock.patch.object(
                    decision_center, "_is_due", side_effect=lambda _s, source, _f: source != "search"
                ),
                mock.patch.object(decision_center, "_sync_funnel", return_value=2),
                mock.patch.object(decision_center, "_sync_advertising", side_effect=ValueError("boom")),
                mock.patch.object(decision_center, "_record_sync") as record,
            ):
                result = decision_center.sync_store(
                    "store", sources=["funnel", "search", "advertising", "wrong"]
                )
            statuses = {row["source"]: row["status"] for row in result["results"]}
            self.assertEqual(statuses, {"funnel": "success", "search": "fresh", "advertising": "error"})
            self.assertEqual(record.call_count, 4)

            with (
                mock.patch.object(decision_center.wb_tokens, "has_token", return_value=True),
                mock.patch.object(decision_center, "sync_store", return_value={"ok": True}) as sync,
            ):
                self.assertEqual(decision_center.sync_many(["store", "unknown"]), {"store": {"ok": True}})
                self.assertIn("store", decision_center.sync_all())
            self.assertEqual(sync.call_count, 2)

    def test_build_products_from_sources(self) -> None:
        key = ("store", 10)
        local = {
            key: {
                "store_slug": "store",
                "nm_id": 10,
                "article": "10",
                "name": "Product",
                "image_url": "image",
                "stock": 15,
                "price_values": [1000, 1200],
                "cost_values": [300],
                "commission_values": [15],
            }
        }
        current = {key: {"orders": 20, "sold": 10, "cancels": 2, "revenue": 18000}}
        previous = {key: {"orders": 10}}
        cached = {
            key: {
                "orders": 20,
                "buyouts": 10,
                "cancels": 2,
                "order_sum": 20000,
                "views": 100,
                "carts": 25,
                "search_orders": 22,
                "rating": 4.6,
                "delivery_days": 2,
                "order_growth": 50,
                "visibility": 30,
                "avg_position": 40,
                "ad_impressions": 1000,
                "ad_clicks": 20,
                "ad_spend": 500,
                "ad_orders": 3,
                "funnel_synced_at": "2026-01-01",
            }
        }
        with (
            mock.patch.object(decision_center, "STORES", {"store": {"name": "Store"}}),
            mock.patch.object(decision_center, "_local_products", return_value=local),
            mock.patch.object(decision_center, "_sales_period", side_effect=[current, previous]),
            mock.patch.object(decision_center, "_cached_metrics", return_value=cached),
        ):
            product = decision_center._build_products(["store"])[0]
        self.assertEqual(product["name"], "Product")
        self.assertEqual(product["orders"], 22)
        self.assertAlmostEqual(product["drr"], 0.055)
        self.assertFalse(product["costModelled"])
        self.assertGreater(product["health"], 0)

        with (
            mock.patch.object(decision_center, "STORES", {"store": {}}),
            mock.patch.object(decision_center, "_local_products", return_value={}),
            mock.patch.object(
                decision_center, "_sales_period", side_effect=[{key: {"orders": 2, "revenue": 0}}, {}]
            ),
            mock.patch.object(decision_center, "_cached_metrics", return_value={}),
        ):
            modelled = decision_center._build_products(["store"])[0]
        self.assertTrue(modelled["costModelled"])
        self.assertEqual(modelled["price"], 1)

    def test_all_opportunity_rules(self) -> None:
        base = {
            "store": "store",
            "storeName": "Store",
            "nmId": 1,
            "article": "1",
            "name": "Product",
            "imageUrl": "",
            "price": 1000,
            "profitShare": 0.3,
            "stock": 50,
            "stockDays": 40,
            "orders": 20,
            "buyouts": 18,
            "cancels": 1,
            "revenue": 20000,
            "weeklyOrders": 5,
            "buyoutRate": 0.9,
            "views": 200,
            "carts": 60,
            "cartRate": 0.3,
            "checkoutRate": 0.33,
            "rating": 4.7,
            "deliveryDays": 2,
            "growth": 0,
            "visibility": 50,
            "avgPosition": 20,
            "estimatedReach": 4000,
            "adImpressions": 1000,
            "adClicks": 40,
            "adSpend": 200,
            "adOrders": 5,
            "ctr": 0.04,
            "drr": 0.1,
            "health": 80,
            "dataUpdatedAt": "now",
        }

        def product(index, **changes):
            return {**base, "nmId": index, "article": str(index), "name": f"P{index}", **changes}

        products = [
            product(1, stock=1, stockDays=2),
            product(2, stock=100, stockDays=120),
            product(3, adSpend=1000, drr=0.8),
            product(4, adImpressions=2000, adClicks=4, ctr=0.002),
            product(5, views=1000, carts=20, cartRate=0.02, checkoutRate=0.5),
            product(6, views=200, carts=100, cartRate=0.5, orders=5, checkoutRate=0.05),
            product(7, orders=30, buyouts=10, buyoutRate=1 / 3),
            product(8, avgPosition=60, orders=12, cartRate=0.3),
            product(9, growth=50, stockDays=20),
            product(10, adImpressions=1000, adClicks=60, ctr=0.06, cartRate=0.4, checkoutRate=0.5),
        ]
        states = {"store:1:stockout": "completed"}
        opportunities = decision_center._opportunities(products, states)
        slugs = {item["fingerprint"].rsplit(":", 1)[-1] for item in opportunities}
        self.assertTrue(
            {
                "stockout",
                "slow-stock",
                "ad-waste",
                "ctr",
                "cart-rate",
                "checkout",
                "buyout",
                "search-position",
                "growth-stock",
            }.issubset(slugs)
        )
        completed = next(item for item in opportunities if item["fingerprint"] == "store:1:stockout")
        self.assertEqual(completed["status"], "completed")
        self.assertLessEqual(len(opportunities), 60)

    def test_dashboard_summary_reallocation_and_empty(self) -> None:
        products = [
            {
                "store": "a",
                "storeName": "A",
                "nmId": 1,
                "name": "Donor",
                "estimatedReach": 1000,
                "views": 100,
                "carts": 30,
                "orders": 5,
                "buyouts": 4,
                "stock": 10,
                "health": 40,
                "stockDays": 5,
                "adSpend": 1000,
                "drr": 0.8,
                "growth": 0,
                "checkoutRate": 0.2,
                "dataUpdatedAt": "2026-01-01",
            },
            {
                "store": "b",
                "storeName": "B",
                "nmId": 2,
                "name": "Receiver",
                "estimatedReach": 500,
                "views": 80,
                "carts": 20,
                "orders": 8,
                "buyouts": 7,
                "stock": 20,
                "health": 80,
                "stockDays": 30,
                "adSpend": 0,
                "drr": 0.1,
                "growth": 20,
                "checkoutRate": 0.4,
                "dataUpdatedAt": "2026-02-01",
            },
        ]
        opportunities = [
            {"status": "new", "expectedRevenue": 1000, "expectedProfit": 300, "severity": "critical"},
            {"status": "completed", "expectedRevenue": 500, "expectedProfit": 100, "severity": "medium"},
        ]
        with (
            mock.patch.object(decision_center, "STORES", {"a": {}, "b": {}}),
            mock.patch.object(decision_center, "_build_products", return_value=products),
            mock.patch.object(decision_center, "_action_states", return_value={}),
            mock.patch.object(decision_center, "_opportunities", return_value=opportunities),
            mock.patch.object(decision_center, "_sync_summary", return_value=[]),
        ):
            result = decision_center.dashboard(["a", "b", "wrong"])
        self.assertEqual(result["summary"]["decisions"], 1)
        self.assertEqual(len(result["reallocations"]), 1)
        self.assertEqual(result["meta"]["lastAnalyticsAt"], "2026-02-01")


if __name__ == "__main__":
    unittest.main()
