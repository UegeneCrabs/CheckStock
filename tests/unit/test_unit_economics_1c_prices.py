import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from app import db
from app import unit_economics_1c_prices as prices
from app.repositories import core

NOW = "2026-08-19T08:00:00+00:00"


class UnitEconomics1CPricesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = mock.patch.object(core, "DB_PATH", Path(self.temp.name) / "prices.sqlite3")
        self.path_patch.start()
        self.wallet_discount_patch = mock.patch.object(
            prices.wb_api,
            "get_default_wallet_discount_percent",
            return_value=2,
        )
        self.wallet_discount_patch.start()
        db.init_db()

    def tearDown(self) -> None:
        self.wallet_discount_patch.stop()
        self.path_patch.stop()
        self.temp.cleanup()

    def test_storefront_rows_use_product_plus_logistics_and_ignore_basic_price(self) -> None:
        rows = prices._storefront_price_rows(
            [
                {
                    "id": 367080326,
                    "sizes": [
                        {
                            "origName": "0",
                            "optionId": 537551321,
                            "price": {"basic": 912100, "product": 203400, "logistics": 100},
                            "saleConditions": 128,
                        }
                    ],
                }
            ]
        )

        self.assertEqual(rows[0]["customer_price_with_spp"], 2035)
        self.assertNotIn("seller_base_price", rows[0])
        self.assertEqual(rows[0]["size_id"], 537551321)
        self.assertEqual(rows[0]["sale_conditions"], 128)

    def test_wallet_price_matches_wb_rounding_and_respects_sale_ban(self) -> None:
        self.assertEqual(prices.calculate_wallet_price(2612, 2), 2559)
        self.assertIsNone(prices.calculate_wallet_price(2612, 2, prices.WALLET_SALE_BAN))
        self.assertIsNone(prices.calculate_wallet_price(2612, 2, prices.POSTPAID_BOOKING))

    def test_daily_price_schema_contains_wallet_price(self) -> None:
        with core.get_connection() as connection:
            self.assertIn(
                "customer_price_with_wallet",
                connection.column_names("unit_economics_1c_wb_daily_prices"),
            )

    def test_seller_rows_keep_base_discounted_and_club_prices(self) -> None:
        rows = prices._seller_price_rows(
            [
                {
                    "nmID": 371727738,
                    "vendorCode": "test",
                    "sizes": [
                        {
                            "sizeID": 543176207,
                            "price": 4403,
                            "discountedPrice": 1232.84,
                            "clubDiscountedPrice": 1232.84,
                            "techSizeName": "0",
                        }
                    ],
                }
            ]
        )

        self.assertEqual(rows[0]["retail_price"], 1232.84)
        self.assertEqual(rows[0]["seller_base_price"], 4403)
        self.assertEqual(rows[0]["club_discounted_price"], 1232.84)

    def test_discount_is_calculated_from_unchanged_base_price(self) -> None:
        discount, calculated = prices.calculate_seller_discount(1000, 800)

        self.assertEqual(discount, 20)
        self.assertEqual(calculated, 800)

        discount, calculated = prices.calculate_seller_discount(1000, 794)
        self.assertEqual(discount, 21)
        self.assertEqual(calculated, 790)

        with self.assertRaisesRegex(prices.PriceChangeError, "выше базовой"):
            prices.calculate_seller_discount(1000, 1100)

    def test_submit_price_changes_balances_base_price_and_discount(self) -> None:
        db.upsert_unit_economics_1c_daily_prices(
            [
                {
                    "store_slug": "rimili",
                    "article": "371727738",
                    "day": "2026-08-19",
                    "marketplace": "WB",
                    "nm_id": "371727738",
                    "currency": "RUB",
                    "retail_price": 900,
                    "customer_price_with_spp": 630,
                    "customer_price_with_wallet": 617,
                    "customer_price_orders_count": 0,
                    "updated_at": NOW,
                }
            ]
        )
        wb_goods = [
            {
                "nmID": 371727738,
                "editableSizePrice": False,
                "sizes": [{"sizeID": 1, "price": 1000, "discountedPrice": 900}],
            }
        ]
        changes = [{"article": "371727738", "target_price": 800}]

        with (
            mock.patch.object(prices.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(
                prices.wb_api, "get_goods_prices_by_nm_ids", return_value=wb_goods
            ) as get_prices,
            mock.patch.object(
                prices.wb_api,
                "upload_goods_prices_and_discounts",
                return_value={"id": 12345, "alreadyExists": False},
            ) as upload,
        ):
            report = prices.submit_price_changes("rimili", changes)

        get_prices.assert_called_once_with("token", [371727738])
        upload.assert_called_once_with("token", [{"nmID": 371727738, "price": 920, "discount": 13}])
        self.assertTrue(report["ok"])
        self.assertEqual(report["upload_id"], 12345)
        self.assertEqual(report["accepted"][0]["display_retail_price"], 800)
        self.assertEqual(report["accepted"][0]["predicted_spp_price"], 560)

    def test_small_spp_change_moves_base_price_instead_of_losing_one_ruble(self) -> None:
        plan = prices._plan_price_change(
            {
                "nmID": 371727738,
                "discount": 50,
                "sizes": [{"price": 2000, "discountedPrice": 1000}],
            },
            {
                "retail_price": 1000,
                "customer_price_with_spp": 699,
                "customer_price_with_wallet": 685,
            },
            {"target_kind": "spp", "target_price": 700},
            2,
        )

        self.assertEqual(plan["base_price"], 2002)
        self.assertEqual(plan["discount"], 50)
        self.assertEqual(plan["predicted_spp_price"], 700)

    def test_large_price_change_keeps_balanced_base_and_discount(self) -> None:
        plan = prices._plan_price_change(
            {
                "nmID": 371727738,
                "discount": 50,
                "sizes": [{"price": 2000, "discountedPrice": 1000}],
            },
            {
                "retail_price": 1000,
                "customer_price_with_spp": 699,
                "customer_price_with_wallet": 685,
            },
            {"target_kind": "retail", "target_price": 2000},
            2,
        )

        self.assertEqual(plan["display_retail_price"], 2000)
        self.assertGreater(plan["discount"], 0)
        self.assertLess(plan["discount"], 50)
        self.assertGreater(plan["base_price"], 2000)
        self.assertLess(plan["base_price"], 4000)

    def test_wallet_target_is_reached_to_the_ruble(self) -> None:
        plan = prices._plan_price_change(
            {
                "nmID": 371727738,
                "discount": 50,
                "sizes": [{"price": 2000, "discountedPrice": 1000}],
            },
            {
                "retail_price": 1000,
                "customer_price_with_spp": 699,
                "customer_price_with_wallet": 685,
            },
            {"target_kind": "wallet", "target_price": 700},
            2,
        )

        self.assertEqual(plan["predicted_wallet_price"], 700)
        self.assertEqual(plan["achieved_target_price"], 700)

    def test_retail_target_keeps_explicit_kopecks(self) -> None:
        plan = prices._plan_price_change(
            {
                "nmID": 498276614,
                "discount": 51,
                "sizes": [{"price": 1946, "discountedPrice": 953.54}],
            },
            {
                "retail_price": 953.54,
                "customer_price_with_spp": 667,
                "customer_price_with_wallet": 653,
            },
            {"target_kind": "retail", "target_price": 953.54},
            2,
        )

        self.assertEqual(plan["base_price"], 1946)
        self.assertEqual(plan["discount"], 51)
        self.assertEqual(plan["achieved_target_price"], 953.54)

    def test_price_upload_is_confirmed_before_automatic_sync(self) -> None:
        report = {
            "ok": True,
            "sent": 1,
            "upload_id": 12345,
            "accepted": [
                {
                    "product_id": "rimili:371727738",
                    "article": "371727738",
                    "nm_id": "371727738",
                }
            ],
            "errors": [],
        }
        with (
            mock.patch.object(prices.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(
                prices.wb_api,
                "get_price_upload_status",
                side_effect=[None, {"uploadID": 12345, "status": 3}],
            ) as status,
            mock.patch.object(prices.time, "sleep") as sleep,
            mock.patch.object(prices, "sync_store", return_value={"ok": True, "rows": 1}) as sync,
        ):
            finalized = prices.finalize_price_change_report("rimili", report)

        self.assertEqual(status.call_count, 2)
        self.assertEqual(
            sleep.call_args_list,
            [
                mock.call(prices.PRICE_UPLOAD_STATUS_PAUSE_SECONDS),
                mock.call(prices.PRICE_SYNC_AFTER_UPLOAD_DELAY_SECONDS),
            ],
        )
        sync.assert_called_once_with("rimili")
        self.assertTrue(finalized["price_data_refreshed"])
        self.assertEqual(finalized["upload_status"]["status"], 3)

    def test_partial_price_upload_keeps_failed_product_out_of_accepted(self) -> None:
        report = {
            "ok": True,
            "sent": 2,
            "upload_id": 12345,
            "accepted": [
                {"product_id": "rimili:1", "article": "1", "nm_id": "1"},
                {"product_id": "rimili:2", "article": "2", "nm_id": "2"},
            ],
            "errors": [],
        }
        with (
            mock.patch.object(
                prices,
                "wait_for_price_upload",
                return_value={"uploadID": 12345, "status": 5},
            ),
            mock.patch.object(prices.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(
                prices.wb_api,
                "get_price_upload_details",
                return_value=[{"nmID": 2, "errorText": "цена в карантине"}],
            ),
            mock.patch.object(prices.time, "sleep"),
            mock.patch.object(prices, "sync_store", return_value={"ok": True, "rows": 1}),
        ):
            finalized = prices.finalize_price_change_report("rimili", report)

        self.assertEqual([item["article"] for item in finalized["accepted"]], ["1"])
        self.assertEqual(finalized["errors"][0]["article"], "2")
        self.assertTrue(finalized["price_data_refreshed"])

    def test_sync_uses_storefront_prices_without_loading_orders(self) -> None:
        db.replace_catalog(
            "rimili",
            "WB",
            [
                {"article": "371727738", "barcode": "1", "name": "Первый"},
                {"article": "371727739", "barcode": "2", "name": "Второй"},
            ],
            NOW,
        )
        storefront = {
            "products": [
                {
                    "id": 371727738,
                    "reviewRating": 4.8,
                    "feedbacks": 406,
                    "sizes": [{"optionId": 1, "price": {"product": 203400, "logistics": 0}}],
                },
                {
                    "id": 371727739,
                    "sizes": [{"optionId": 2, "price": {"product": 85000, "logistics": 0}}],
                },
            ],
            "failed_nm_ids": [],
            "errors": [],
        }
        seller_prices = [
            {
                "nmID": 371727738,
                "sizes": [
                    {
                        "sizeID": 1,
                        "price": 4403,
                        "discountedPrice": 1232.84,
                        "clubDiscountedPrice": 1200,
                    }
                ],
            },
            {
                "nmID": 371727739,
                "sizes": [{"sizeID": 2, "discountedPrice": 1000}],
            },
        ]
        with (
            mock.patch.object(prices.wb_api, "get_storefront_products", return_value=storefront),
            mock.patch.object(prices.wb_api, "get_goods_prices", return_value=seller_prices),
            mock.patch.object(prices.wb_tokens, "has_token", return_value=True),
            mock.patch.object(prices.wb_tokens, "get_token", return_value="token"),
        ):
            report = prices.sync_store("rimili", date(2026, 8, 19))

        self.assertTrue(report["ok"])
        self.assertEqual(report["storefront_rows"], 2)
        latest = {row["article"]: row for row in db.get_unit_economics_1c_latest_daily_prices(("rimili",))}
        self.assertEqual(latest["371727738"]["retail_price"], 1232.84)
        self.assertEqual(latest["371727738"]["seller_base_price"], 4403)
        self.assertEqual(latest["371727738"]["club_discounted_price"], 1200)
        self.assertEqual(latest["371727738"]["customer_price_with_spp"], 2034)
        self.assertEqual(latest["371727738"]["customer_price_with_wallet"], 1993)
        self.assertEqual(latest["371727738"]["customer_price_orders_count"], 0)
        self.assertIsNone(latest["371727738"]["customer_price_window_days"])
        reputation = db.get_unit_economics_1c_latest_product_reputation(("rimili",))
        first_reputation = next(item for item in reputation if item["article"] == "371727738")
        self.assertEqual(first_reputation["rating"], 4.8)
        self.assertEqual(first_reputation["reviews_count"], 406)
        self.assertEqual(report["reputation_rows"], 1)

        with (
            mock.patch.object(prices.wb_api, "get_storefront_products", return_value=storefront),
            mock.patch.object(prices.wb_api, "get_goods_prices", return_value=seller_prices),
            mock.patch.object(prices.wb_tokens, "has_token", return_value=True),
            mock.patch.object(prices.wb_tokens, "get_token", return_value="token"),
        ):
            prices.sync_store("rimili", date(2026, 8, 20))
        history = db.get_unit_economics_1c_daily_price_history("rimili", "371727738")
        self.assertEqual([row["day"] for row in history], ["2026-08-19", "2026-08-20"])

    def test_sync_preserves_reliable_spp_factor_when_storefront_returns_seller_price(self) -> None:
        db.replace_catalog(
            "rimili",
            "WB",
            [{"article": "498276614", "barcode": "1", "name": "Товар"}],
            NOW,
        )
        db.upsert_unit_economics_1c_daily_prices(
            [
                {
                    "store_slug": "rimili",
                    "article": "498276614",
                    "day": "2026-08-20",
                    "marketplace": "WB",
                    "nm_id": "498276614",
                    "currency": "RUB",
                    "retail_price": 758.94,
                    "customer_price_with_spp": 531,
                    "customer_price_orders_count": 0,
                    "updated_at": NOW,
                },
                {
                    "store_slug": "rimili",
                    "article": "498276614",
                    "day": "2026-08-21",
                    "marketplace": "WB",
                    "nm_id": "498276614",
                    "currency": "RUB",
                    "retail_price": 953.54,
                    "customer_price_with_spp": 953,
                    "customer_price_orders_count": 0,
                    "updated_at": NOW,
                },
            ]
        )
        storefront = {
            "products": [
                {
                    "id": 498276614,
                    "sizes": [{"optionId": 1, "price": {"product": 95300, "logistics": 0}}],
                }
            ],
            "failed_nm_ids": [],
            "errors": [],
        }
        seller_prices = [
            {
                "nmID": 498276614,
                "sizes": [{"sizeID": 1, "price": 1946, "discountedPrice": 953.54}],
            }
        ]
        with (
            mock.patch.object(prices.wb_api, "get_storefront_products", return_value=storefront),
            mock.patch.object(prices.wb_api, "get_goods_prices", return_value=seller_prices),
            mock.patch.object(prices.wb_tokens, "has_token", return_value=True),
            mock.patch.object(prices.wb_tokens, "get_token", return_value="token"),
        ):
            report = prices.sync_store("rimili", date(2026, 8, 21))

        latest = db.get_unit_economics_1c_latest_daily_prices(("rimili",))[0]
        self.assertEqual(latest["retail_price"], 953.54)
        self.assertEqual(latest["customer_price_with_spp"], 667)
        self.assertEqual(report["estimated_spp_rows"], 1)

    def test_missing_storefront_price_is_not_replaced_from_orders(self) -> None:
        db.replace_catalog(
            "rimili",
            "WB",
            [
                {"article": "371727738", "barcode": "1", "name": "Первый"},
                {"article": "371727739", "barcode": "2", "name": "Второй"},
            ],
            NOW,
        )
        storefront = {
            "products": [
                {
                    "id": 371727738,
                    "sizes": [{"optionId": 1, "price": {"product": 98600, "logistics": 0}}],
                },
                {
                    "id": 371727739,
                    "totalQuantity": 0,
                    "sizes": [{"optionId": 2, "stocks": []}],
                },
            ],
            "failed_nm_ids": [],
            "errors": [],
        }
        seller_prices = [
            {"nmID": 371727738, "sizes": [{"sizeID": 1, "discountedPrice": 1232.84}]},
            {"nmID": 371727739, "sizes": [{"sizeID": 2, "discountedPrice": 900}]},
        ]
        with (
            mock.patch.object(prices.wb_api, "get_storefront_products", return_value=storefront),
            mock.patch.object(prices.wb_api, "get_goods_prices", return_value=seller_prices),
            mock.patch.object(prices.wb_tokens, "has_token", return_value=True),
            mock.patch.object(prices.wb_tokens, "get_token", return_value="token"),
        ):
            report = prices.sync_store("rimili", date(2026, 8, 19))

        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["storefront_rows"], 1)
        self.assertEqual(report["unresolved_rows"], 1)
        self.assertEqual(report["storefront_returned_products"], 2)
        self.assertEqual(report["storefront_priced_products"], 1)
        self.assertEqual(report["storefront_without_price_products"], 1)
        self.assertEqual(report["storefront_out_of_stock_products"], 1)
        latest = {row["article"]: row for row in db.get_unit_economics_1c_latest_daily_prices(("rimili",))}
        self.assertEqual(latest["371727738"]["customer_price_with_spp"], 986)
        self.assertEqual(latest["371727738"]["retail_price"], 1232.84)
        self.assertNotIn("371727739", latest)

    def test_failed_storefront_without_token_is_exposed_in_sync_state(self) -> None:
        db.replace_catalog(
            "tris",
            "WB",
            [{"article": "371727738", "barcode": "1", "name": "Первый"}],
            NOW,
        )
        storefront = {
            "products": [],
            "failed_nm_ids": ["371727738"],
            "errors": ["временная ошибка"],
        }
        with (
            mock.patch.object(prices.wb_api, "get_storefront_products", return_value=storefront),
            mock.patch.object(prices.wb_tokens, "has_token", return_value=False),
        ):
            report = prices.sync_store("tris", date(2026, 8, 19))
        self.assertFalse(report["ok"])
        state = db.list_unit_economics_1c_price_sync_states(("tris",))[0]
        self.assertEqual(state["status"], "error")
        self.assertIn("токена", state["error"])

    def test_missing_seller_scope_uses_latest_order_price(self) -> None:
        db.replace_catalog(
            "rimili",
            "WB",
            [{"article": "949735537", "barcode": "1", "name": "Товар"}],
            NOW,
        )
        storefront = {
            "products": [
                {
                    "id": 949735537,
                    "sizes": [{"optionId": 1, "price": {"product": 338900, "logistics": 0}}],
                }
            ],
            "failed_nm_ids": [],
            "errors": [],
        }
        order_rows = [
            {
                "ordered_at": "2026-08-16T20:19:12+03:00",
                "barcode": "1",
                "raw_json": '{"nmId":949735537,"priceWithDisc":4390,"finishedPrice":3432}',
            }
        ]
        with (
            mock.patch.object(prices.wb_api, "get_storefront_products", return_value=storefront),
            mock.patch.object(prices.wb_tokens, "has_token", return_value=False),
            mock.patch.object(
                prices.db,
                "get_unit_economics_1c_wb_order_price_rows",
                return_value=order_rows,
            ),
        ):
            report = prices.sync_store("rimili", date(2026, 8, 19))

        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "fallback")
        self.assertEqual(report["retail_fallback_rows"], 1)
        latest = db.get_unit_economics_1c_latest_daily_prices(("rimili",))[0]
        self.assertEqual(latest["retail_price"], 4390)
        self.assertEqual(latest["customer_price_with_spp"], 3389)

    def test_spp_only_mode_does_not_load_retail_price(self) -> None:
        db.replace_catalog(
            "rimili",
            "WB",
            [{"article": "371727738", "barcode": "1", "name": "Первый"}],
            NOW,
        )
        storefront = {
            "products": [
                {
                    "id": 371727738,
                    "totalQuantity": 0,
                    "sizes": [{"optionId": 1, "stocks": []}],
                }
            ],
            "failed_nm_ids": [],
            "errors": [],
        }
        seller_prices = [{"nmID": 371727738, "sizes": [{"sizeID": 1, "discountedPrice": 1232.84}]}]
        with (
            mock.patch.object(prices.wb_api, "get_storefront_products", return_value=storefront),
            mock.patch.object(prices.wb_api, "get_goods_prices", return_value=seller_prices) as seller_sync,
            mock.patch.object(prices.wb_tokens, "has_token", return_value=True),
            mock.patch.object(prices.wb_tokens, "get_token", return_value="token"),
        ):
            report = prices.sync_store(
                "rimili",
                date(2026, 8, 19),
                load_retail_prices=False,
            )

        seller_sync.assert_not_called()
        self.assertFalse(report["retail_price_requested"])
        self.assertEqual(report["unresolved_rows"], 1)

    def test_wallet_only_refresh_preserves_last_price_when_discount_request_fails(self) -> None:
        db.replace_catalog(
            "rimili",
            "WB",
            [{"article": "153985484", "barcode": "1", "name": "Товар"}],
            NOW,
        )
        db.upsert_unit_economics_1c_daily_prices(
            [
                {
                    "store_slug": "rimili",
                    "article": "153985484",
                    "day": "2026-08-19",
                    "marketplace": "WB",
                    "nm_id": "153985484",
                    "currency": "RUB",
                    "retail_price": 3593.52,
                    "customer_price_with_spp": 2612,
                    "customer_price_with_wallet": 2559,
                    "customer_price_orders_count": 0,
                    "updated_at": NOW,
                }
            ]
        )
        storefront = {
            "products": [
                {
                    "id": 153985484,
                    "sizes": [
                        {
                            "optionId": 1,
                            "price": {"product": 260000, "logistics": 0},
                            "saleConditions": 0,
                        }
                    ],
                }
            ],
            "failed_nm_ids": [],
            "errors": [],
        }
        with (
            mock.patch.object(prices.wb_api, "get_storefront_products", return_value=storefront),
            mock.patch.object(
                prices.wb_api,
                "get_default_wallet_discount_percent",
                side_effect=prices.wb_api.WBApiError(503),
            ),
        ):
            report = prices.sync_store(
                "rimili",
                date(2026, 8, 19),
                load_retail_prices=False,
                record_state=False,
            )

        latest = db.get_unit_economics_1c_latest_daily_prices(("rimili",))[0]
        self.assertEqual(latest["customer_price_with_spp"], 2600)
        self.assertEqual(latest["customer_price_with_wallet"], 2559)
        self.assertFalse(report["wallet_discount_ok"])
        self.assertEqual(db.list_unit_economics_1c_price_sync_states(("rimili",)), [])


if __name__ == "__main__":
    unittest.main()
