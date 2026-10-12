"""Account selection, real sheet headers, and failure-before-publication regressions."""

import os
import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

import dotenv

with (
    patch.object(dotenv, "dotenv_values", return_value={}),
    patch.dict(os.environ, {"CHECKSTOCK_DATABASE_URL": ""}),
):
    from app.core.stores import STORES
    from app.exports import fbo_transit, project_sheet
    from app.exports.project_scope import stores_for_marketplace


class ProjectExportScopeTests(unittest.TestCase):
    def test_all_three_loaders_skip_only_nonexistent_yandex_account(self):
        for market, loader in (("YANDEX MARKET", "_yandex"), ("OZON", "_ozon"), ("WB", "_wb")):
            with self.subTest(market=market):
                expected = tuple(s for s in STORES if market != "YANDEX MARKET" or s != "toyka")
                with (
                    patch.object(
                        project_sheet.legacy, "_combined_stock_snapshot", return_value=([], {}, [])
                    ) as stock,
                    patch.object(
                        project_sheet.legacy, "_combined_fbs_order_totals", return_value={"001": 3}
                    ) as orders,
                    patch.object(fbo_transit, loader, return_value=fbo_transit.TransitSnapshot()) as transit,
                ):
                    self.assertEqual(
                        tuple(s.store_slug for s in project_sheet._stock_snapshot(market)), expected
                    )
                    self.assertEqual(tuple(project_sheet._order_totals(market)), expected)
                    self.assertEqual(tuple(fbo_transit.load(market)), expected)
                    self.assertEqual([call.args[0][0] for call in stock.call_args_list], list(expected))
                    self.assertEqual([call.args[0][0] for call in orders.call_args_list], list(expected))
                    self.assertCountEqual([call.args[0] for call in transit.call_args_list], expected)

    def test_existing_account_failure_still_prevents_every_sheet_write(self):
        def orders(stores, marketplace, **kwargs):
            if stores == ("gogol",):
                raise KeyError("missing credentials")
            return {}

        for kind in ("fbs_orders", "fbo_transit", None):
            with self.subTest(kind=kind):
                target = project_sheet.MarketplaceExportTarget(
                    "YANDEX MARKET",
                    "Stock",
                    "Orders",
                    "https://docs.google.com/spreadsheets/d/test/edit",
                    fbo_sheet_name="Transit",
                )
                settings = project_sheet.default_settings()
                settings = replace(
                    settings,
                    targets=tuple(
                        target if t.marketplace == target.marketplace else t for t in settings.targets
                    ),
                )
                with (
                    patch.object(project_sheet, "get_settings", return_value=settings),
                    patch.object(project_sheet.legacy, "_combined_fbs_order_totals", side_effect=orders),
                    patch.object(fbo_transit, "_yandex", side_effect=KeyError("missing credentials")),
                    patch.object(project_sheet.legacy, "_google_service") as google,
                ):
                    with self.assertRaises(project_sheet.StockSheetExportError):
                        project_sheet.export_all(marketplace="YANDEX MARKET", export_kind=kind)
                    google.assert_not_called()

    def plan(self, header="Магазин", column="I"):
        service = MagicMock()
        rows = [
            ["Title"],
            [""],
            [header, "Supplier", "Category", "BARCODE", "ARTICLE", "NAME", "From", "To", "FBS"],
            ["RIMILI", "", "", "000123", "001", "Product"],
            ["TOYKA", "", "", "000456", "001", "Other"],
            ["TRIS", "", "", "000789", "002", "No orders"],
        ]
        service.spreadsheets.return_value.values.return_value.get.return_value.execute.side_effect = [
            {"values": rows},
            {"values": [["old"], ["old"], ["old"], ['=""']]},
        ]
        plan = project_sheet._plan_quantities(
            service,
            "test",
            "Stock",
            column,
            {
                store: {"001": 7} if store == "rimili" else {}
                for store in stores_for_marketplace("YANDEX MARKET")
            },
            {"properties": {"sheetId": 1, "gridProperties": {"columnCount": 12, "rowCount": 100}}},
            label="FBS",
        )
        return plan, service

    def test_shop_header_and_project_aliases_match_without_writing_toyka_zero(self):
        for header in ("Магазин", " магазин ", "Проект", "PROJECT"):
            for column in ("G", "H", "I"):
                with self.subTest(header=header, column=column):
                    plan, _ = self.plan(header, column)
                    self.assertEqual(plan.clear_range, f"'Stock'!{column}4:{column}7")
                    self.assertEqual(
                        plan.updates,
                        [
                            {"range": f"'Stock'!{column}4:{column}4", "values": [[7]]},
                            {"range": f"'Stock'!{column}6:{column}6", "values": [[0]]},
                        ],
                    )
                    self.assertEqual(plan.row_count, 2)
                    self.assertNotIn("toyka", plan.store_slugs)

    def test_shop_header_is_protected_as_identifier(self):
        with self.assertRaisesRegex(project_sheet.StockSheetExportError, "содержит данные товара"):
            self.plan(column="A")

    def test_ambiguous_project_and_shop_headers_still_rejected(self):
        service = MagicMock()
        service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [["Проект", "Магазин", "ARTICLE"]]
        }
        with self.assertRaisesRegex(project_sheet.StockSheetExportError, "неоднозначные"):
            project_sheet._plan_quantities(service, "test", "Stock", "I", {}, {"properties": {}}, label="FBS")

    def test_missing_inbound_keeps_actual_stocks_and_does_not_fabricate_total(self):
        rows = project_sheet.legacy._stock_export_rows(
            [{"article": "001", "barcode": "000123", "name": "Product"}],
            {
                "ff_stock": {"001": 5},
                "fbs_stock": {"001": 6},
                "fbo_stock": {"001": 7},
                "ff_transit": {"001": 2},
                "mp_inbound": {"001": None},
            },
            [],
        )
        self.assertEqual(rows[0][3:], ["", 5, 6, 7, 2, ""])


if __name__ == "__main__":
    unittest.main()
