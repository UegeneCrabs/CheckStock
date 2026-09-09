import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import db, unit_economics_1c, unit_economics_1c_reference_data, unit_economics_1c_source_data
from app.repositories import core
from app.repositories.unit_economics_data_errors import source_states
from app.unit_economics_data_errors import SOURCE_LABELS, product_errors
from app.web.routers import unit_economics


class ProductDataErrorsTests(unittest.TestCase):
    def product(self, reference=None, prices=None):
        return unit_economics._unit_economics_1c_mock_product(
            "rimili", {"article": "123", "name": "Товар", "fbs_stock": 0,
                       "fbo_stock": 0, "ff_available": 0},
            {"customer_price_with_spp": 100, "customer_price_with_wallet": 90, **(prices or {})},
            product_reference={"category": "Товар", "abc_code": "A", "team_commission_percent": 0,
                               **(reference or {})},
            product_metrics={"funnel_updated_at": "2026-09-08", "buyout_updated_at": "2026-09-08"},
            reputation={"rating": 0, "reviews_count": 0},
            source_states={"unit_economics_1c_advertising": {"ok": True}},
        )

    def test_missing_sources_are_reported_in_detail_and_table(self):
        product = self.product()
        self.assertEqual(product["data_errors"], [
            "Не загружена себестоимость", "Не загружены затраты на ФФ",
            "Не загружена комиссия WB", "Не загружена цена продажи",
        ])
        summary = unit_economics._unit_economics_1c_product_summary(product)
        self.assertEqual(summary["data_errors"], product["data_errors"])

    def test_loaded_zero_is_valid_and_errors_do_not_leak_between_products(self):
        missing = self.product()
        loaded = self.product(
            {"purchase_price": 0, "fulfillment_cost": 0, "subject_commission_percent": 0},
            {"retail_price": 100},
        )
        self.assertEqual(loaded["data_errors"], [])
        self.assertEqual(len(missing["data_errors"]), 4)

    def test_only_missing_or_invalid_fields_are_reported(self):
        for value in (None, "", "invalid"):
            with self.subTest(value=value):
                product = self.product(
                    {"purchase_price": value, "fulfillment_cost": 50, "subject_commission_percent": 20},
                    {"retail_price": 100},
                )
                self.assertEqual(product["data_errors"], ["Не загружена себестоимость"])

    def test_error_clears_when_reference_data_arrives(self):
        reference = {"purchase_price": 100, "subject_commission_percent": 20}
        self.assertEqual(self.product(reference, {"retail_price": 200})["data_errors"],
                         ["Не загружены затраты на ФФ"])
        reference["fulfillment_cost"] = 10
        self.assertEqual(self.product(reference, {"retail_price": 200})["data_errors"], [])

    def test_all_sources_have_missing_data_checks(self):
        errors = product_errors({}, {}, {}, {}, {}, {})
        for label in ("себестоимость", "затраты на ФФ", "комиссия WB", "маркетинговых затрат",
                      "цена продажи", "цена с СПП", "Кошелька", "остатки FBS", "остатки FBO",
                      "остатки ФФ", "рейтинг", "отзывов", "категория", "ABC", "воронка",
                      "выкупа", "рекламы"):
            with self.subTest(label=label):
                self.assertTrue(any(label in error for error in errors))

    def test_failed_refresh_does_not_leak_to_products_with_cached_values(self):
        product = {"fbs_stock": 0, "fbo_stock": 0, "ff_available": 0}
        prices = {
            "retail_price": 200,
            "customer_price_with_spp": 180,
            "customer_price_with_wallet": 170,
        }
        reference = {
            "purchase_price": 100,
            "fulfillment_cost": 10,
            "team_commission_percent": 5,
            "subject_commission_percent": 20,
            "category": "Товар",
            "abc_code": "A",
        }
        metrics = {"funnel_updated_at": "2026-09-08", "buyout_updated_at": "2026-09-08"}
        failed_states = {scope: {"ok": False} for scope in SOURCE_LABELS}

        errors = product_errors(product, prices, reference, metrics, {}, failed_states)

        self.assertFalse(any("ошибка обновления" in error for error in errors))

    def test_failed_refresh_is_shown_only_for_the_missing_product_source(self):
        product = {"fbs_stock": 0, "fbo_stock": 0, "ff_available": 0}
        prices = {
            "retail_price": None,
            "customer_price_with_spp": 180,
            "customer_price_with_wallet": 170,
        }
        reference = {
            "purchase_price": 100,
            "fulfillment_cost": 10,
            "team_commission_percent": 5,
            "subject_commission_percent": 20,
            "category": "Товар",
            "abc_code": "A",
        }
        metrics = {"funnel_updated_at": "2026-09-08", "buyout_updated_at": "2026-09-08"}
        failed_states = {
            "unit_economics_1c_prices": {"ok": False},
            "unit_economics_1c_wallet": {"ok": False},
        }

        errors = product_errors(product, prices, reference, metrics, {}, failed_states)

        self.assertIn("Цены WB: ошибка обновления, данные могут быть устаревшими", errors)
        self.assertNotIn("Цены СПП и WB Кошелька: ошибка обновления, данные могут быть устаревшими", errors)

    def test_reference_refresh_error_is_scoped_to_product_with_missing_reference(self):
        failed = {"unit_economics_1c_source": {"ok": False}}
        loaded = {
            "purchase_price": 100,
            "fulfillment_cost": 10,
            "team_commission_percent": 5,
        }
        incomplete = {**loaded, "purchase_price": None}
        message = "Данные 1С: ошибка обновления, данные могут быть устаревшими"

        self.assertNotIn(message, product_errors({}, {}, loaded, {}, {}, failed))
        self.assertIn(message, product_errors({}, {}, incomplete, {}, {}, failed))

    def test_each_product_scoped_failure_requires_its_own_missing_field(self):
        cases = (
            ("fbs", {"fbs_stock": None}, {}, {}, {}, "Остатки FBS"),
            ("fbo", {"fbo_stock": None}, {}, {}, {}, "Остатки FBO"),
            ("ff", {"ff_available": None}, {}, {}, {}, "Остатки ФФ"),
            ("unit_economics_1c_wallet", {}, {"customer_price_with_wallet": None}, {}, {},
             "Цены СПП и WB Кошелька"),
            ("unit_economics_1c_commissions", {}, {}, {"subject_commission_percent": None}, {},
             "Комиссии WB"),
            ("unit_economics_1c_categories", {}, {}, {"category": ""}, {}, "Категории WB"),
            ("unit_economics_1c_classifications", {}, {}, {"abc_code": ""}, {},
             "ABC-классификация"),
            ("unit_economics_1c_funnel", {}, {}, {}, {"funnel_updated_at": None},
             "Заказы и воронка WB"),
            ("unit_economics_1c_buyout", {}, {}, {}, {"buyout_updated_at": None},
             "Процент выкупа WB"),
        )
        for scope, product, prices, reference, metrics, label in cases:
            with self.subTest(scope=scope):
                message = f"{label}: ошибка обновления, данные могут быть устаревшими"
                errors = product_errors(
                    product, prices, reference, metrics, {}, {scope: {"ok": False}},
                )
                self.assertIn(message, errors)

    def test_store_only_failure_is_not_assigned_to_every_product(self):
        for scope in ("catalog", "unit_economics_1c_advertising"):
            with self.subTest(scope=scope):
                message = f"{SOURCE_LABELS[scope]}: ошибка обновления, данные могут быть устаревшими"
                errors = product_errors({}, {}, {}, {}, {}, {scope: {"ok": False}})
                self.assertNotIn(message, errors)

    def test_non_finite_source_values_are_missing(self):
        for value in (float("nan"), float("inf"), "-inf"):
            self.assertIn("Не загружена себестоимость",
                          product_errors({}, {}, {"purchase_price": value}, {}, {}))

    def test_empty_stock_rows_after_successful_import_are_zero_not_missing(self):
        errors = product_errors({}, {}, {}, {}, {}, {
            "fbs": {"ok": True}, "fbo": {"ok": True}, "ff": {"ok": True},
        })
        self.assertFalse(any("остатки" in error for error in errors))

    def test_source_failure_and_recovery_are_recorded(self):
        with (
            mock.patch.object(unit_economics_1c_source_data, "_sync_all", side_effect=ValueError("offline")),
            mock.patch.object(unit_economics_1c_source_data.db, "record_sync_health") as record,
        ):
            with self.assertRaises(ValueError):
                unit_economics_1c_source_data.sync_all()
            self.assertTrue(record.call_args_list)
            self.assertTrue(all(call.args[3] is False for call in record.call_args_list))
        with (
            mock.patch.object(unit_economics_1c_source_data, "_sync_all", return_value={"ok": True}),
            mock.patch.object(unit_economics_1c_source_data.db, "record_sync_health") as record,
        ):
            unit_economics_1c_source_data.sync_all()
            self.assertTrue(all(call.args[3] is True for call in record.call_args_list))

    def test_reference_failure_is_scoped_to_store(self):
        with mock.patch.object(unit_economics_1c_reference_data.db, "record_sync_health") as record:
            result = unit_economics_1c_reference_data._safe_sync(
                "categories:rimili", mock.Mock(side_effect=ValueError("offline")),
            )
        self.assertFalse(result["ok"])
        record.assert_called_once()
        self.assertEqual(record.call_args.args[:4], ("rimili", "WB", "unit_economics_1c_categories", False))

    def test_wallet_discount_failure_marks_only_affected_store(self):
        with (
            mock.patch.object(unit_economics_1c.price_sync, "sync_stores", return_value={
                "rimili": {"ok": True, "wallet_discount_ok": False, "wallet_error": "offline"},
                "tris": {"ok": True, "wallet_discount_ok": True},
            }),
            mock.patch.object(unit_economics_1c.db, "record_sync_health") as record,
        ):
            unit_economics_1c.sync_wallet_prices(("rimili", "tris"))
        self.assertEqual([(call.args[0], call.args[3]) for call in record.call_args_list],
                         [("rimili", False), ("tris", True)])


class SourceStateRepositoryTests(unittest.TestCase):
    def test_store_and_marketplace_isolation_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            core, "DB_PATH", Path(directory) / "errors.db",
        ):
            db.init_db()
            now = "2026-09-08T10:00:00+00:00"
            db.record_sync_health("rimili", "WB", "unit_economics_1c_source", False, "offline", now)
            db.record_sync_health("tris", "WB", "unit_economics_1c_source", True, None, now)
            db.record_sync_health("tris", "OZON", "fbs", False, "offline", now)
            states = source_states(("rimili", "tris"))
            self.assertFalse(states["rimili"]["unit_economics_1c_source"]["ok"])
            self.assertTrue(states["tris"]["unit_economics_1c_source"]["ok"])
            self.assertNotIn("fbs", states["tris"])
            self.assertEqual(set(source_states(("tris",))), {"tris"})
            self.assertEqual(source_states(()), {})
            db.record_sync_health("rimili", "WB", "unit_economics_1c_source", True, None, now)
            self.assertTrue(source_states(("rimili",))["rimili"]["unit_economics_1c_source"]["ok"])

    def test_successful_empty_advertising_and_ff_import_are_available(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            core, "DB_PATH", Path(directory) / "empty.db",
        ):
            db.init_db()
            now = "2026-09-08T10:00:00+00:00"
            db.record_unit_economics_1c_advertising_sync_state(
                "rimili", status="ok", date_from="2026-09-01", date_to="2026-09-07",
                attempted_at=now, rows_saved=0, campaigns_count=0, error=None,
            )
            db.apply_ff_import_snapshot(
                "rimili", "ФФ", "WB", "sheet", "test", {}, now,
                sheet_url=None, table_title="Остатки", total_rows=0, unmatched=0,
            )
            states = source_states(("rimili",))["rimili"]
            errors = product_errors({}, {}, {}, {}, {}, states)
            self.assertNotIn("Не загружены данные рекламы WB", errors)
            self.assertNotIn("Не загружены остатки ФФ", errors)
