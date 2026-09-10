import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import db, unit_economics_yandex
from app import unit_economics_1c_source_data as wb
from app import unit_economics_yandex_source_data as ym
from app.repositories import core, yandex_assortment
from app.repositories import yandex_source_values as repository

NOW = "2026-09-10T00:00:00+00:00"


def sheet(rows, title="YM"):
    return {
        "title": title,
        "sheet_id": 232771603,
        "rows": [
            [
                "Магазин",
                "ARTICLE",
                "ТЕГ",
                "Артикул поставщика внешний",
                "Себес, руб",
                "Проч.затр, руб",
                "Процент для учета маркетинговых затрат",
                "Менеджер",
            ],
            *rows,
        ],
    }


def row(store="ХочуШар", article="00123", cost="1 220,0", fee="1%"):
    return [store, article, "3 / 0 / SHORT5 / W41 2026", "B / Ф:2 / П:7", cost, "44,9", fee, "Менеджер"]


class YandexSourceDataTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for patcher in (
            mock.patch.object(core, "DB_PATH", Path(temp.name) / "source.db"),
            mock.patch.object(
                yandex_assortment,
                "load_active_products",
                return_value={("rimili", "00123"), ("trusthome", "00123")},
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        db.init_db()

    def test_all_fields_store_aliases_and_same_article_in_different_stores(self):
        data = sheet(
            [
                row(),
                row("BTH", cost="950"),
                row("Гоголь", "G"),
                row("Sokoloff ", "S"),
                row("Rockkiddo", "R"),
                row("TRIS", "T"),
            ]
        )
        report = ym.sync_all([data])
        self.assertEqual(report["saved"], 6)
        values = repository.get_values("rimili")["00123"]
        for key, expected in {
            "purchase_price": 1220,
            "fulfillment_cost": 44.9,
            "team_commission_percent": 1,
            "manager": "Менеджер",
            "goal_week": 3,
            "goal_day": 0,
            "stock_status": "SHORT5",
            "stock_end_week": "W41 2026",
            "abc_code": "B",
            "fact_sales": 2,
            "plan_sales": 7,
            "source_sheet_id": 232771603,
            "source_row": 2,
            "source_sheet_title": "YM",
        }.items():
            self.assertEqual(values[key], expected, key)
        self.assertEqual(repository.get_values("trusthome")["00123"]["purchase_price"], 950)
        for store, article in (("gogol", "G"), ("sokoloff", "S"), ("rockkiddo", "R"), ("tris", "T")):
            self.assertIn(article, repository.get_values(store))
        self.assertTrue(values["synced_at"])

    def test_header_names_not_column_positions_optional_manager_and_zero_values(self):
        data = sheet([row(cost=0)])
        data["rows"] = [list(reversed(values[:-1])) for values in data["rows"]]
        ym.sync_all([data])
        values = repository.get_values("rimili")["00123"]
        self.assertEqual(values["purchase_price"], 0)
        self.assertIsNone(values["manager"])
        self.assertEqual(values["goal_day"], 0)

    def test_current_snapshot_replacement_preserves_wb_data_and_cabinet_settings(self):
        db.replace_catalog("rimili", "WB", [{"article": "00123"}], NOW)
        with core.get_connection() as connection:
            stock_id = connection.execute(
                "SELECT id FROM stock_items WHERE store_slug='rimili' AND marketplace='WB' AND article='00123'"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO unit_economics_1c_source_values (stock_item_id,purchase_price,source_sheet_id,source_sheet_title,source_row,synced_at) VALUES (?,777,1,'WB',2,?)",
                (stock_id, NOW),
            )
            connection.execute(
                "INSERT INTO unit_economics_1c_cabinet_settings (store_slug,marketplace,team_commission_percent,updated_at,updated_by_user_id,updated_by_name) VALUES ('rimili','WB',9,?,1,'WB')",
                (NOW,),
            )
            connection.commit()
            before_wb = [
                dict(row) for row in connection.execute("SELECT * FROM unit_economics_1c_source_values")
            ]
        ym.sync_all([sheet([row(), row("BTH")])])
        ym.sync_all([sheet([row(cost="1500")])])
        self.assertEqual(repository.get_values("trusthome"), {})
        self.assertEqual(repository.get_values("rimili")["00123"]["purchase_price"], 1500)
        self.assertEqual(db.get_unit_economics_1c_cabinet_settings("rimili").team_commission_percent, 9)
        with core.get_connection() as connection:
            self.assertEqual(
                [dict(row) for row in connection.execute("SELECT * FROM unit_economics_1c_source_values")],
                before_wb,
            )
            value = connection.execute(
                "SELECT team_commission_percent FROM unit_economics_1c_cabinet_settings WHERE store_slug='rimili' AND marketplace='YANDEX MARKET'"
            ).fetchone()[0]
        self.assertEqual(value, 1)

    def test_database_failure_rolls_back_snapshot_replacement(self):
        ym.sync_all([sheet([row()])])
        before = repository.get_values("rimili")
        parsed = ym.parse_source_values([sheet([row(cost="888")])])["rows"]
        from sqlalchemy.exc import IntegrityError

        with self.assertRaises(IntegrityError):
            repository.replace_values(parsed + parsed, {}, NOW)
        self.assertEqual(repository.get_values("rimili"), before)

    def test_invalid_source_or_failed_fetch_preserves_previous_snapshot(self):
        ym.sync_all([sheet([row()])])
        before = repository.get_values("rimili")
        for invalid in (
            [],
            [sheet([row()], "WB")],
            [sheet([row("unknown")])],
            [sheet([row(), row(cost="999")])],
            [sheet([]) | {"rows": [["ARTICLE"]]}],
        ):
            with self.subTest(invalid=invalid), self.assertRaises(wb.SourceDataError):
                ym.sync_all(invalid)
            self.assertEqual(repository.get_values("rimili"), before)
        with mock.patch.object(wb, "fetch_sheet_rows", side_effect=wb.SourceDataError("offline")):
            with self.assertRaises(wb.SourceDataError):
                ym.sync_all()
        self.assertEqual(repository.get_values("rimili"), before)

    def test_identical_duplicates_and_commission_choice_match_wb_rules(self):
        report = ym.sync_all(
            [sheet([row(), row(), row(article="456", fee="2%"), row(article="789", fee="2%")])]
        )
        self.assertEqual(report["duplicates"], 1)
        self.assertEqual(report["saved"], 3)
        self.assertEqual(report["commission_conflicts"], {"rimili": {1: 1, 2: 2}})
        with core.get_connection() as connection:
            value = connection.execute(
                "SELECT team_commission_percent FROM unit_economics_1c_cabinet_settings WHERE store_slug='rimili' AND marketplace='YANDEX MARKET'"
            ).fetchone()[0]
        self.assertEqual(value, 2)

    def test_values_survive_catalog_refresh_and_only_visible_assortment_is_shown(self):
        ym.sync_all([sheet([row(), row(article="extra")])])
        self.assertIn("00123", repository.get_values("rimili"))
        db.replace_catalog("rimili", "YANDEX MARKET", [{"article": "00123"}, {"article": "extra"}], NOW)
        products = unit_economics_yandex.load_products(("rimili",))
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["details"]["purchase_cost"], 1220)
        self.assertEqual(products[0]["details"]["fulfillment_cost"], 44.9)
        self.assertEqual(
            products[0]["tag_data"],
            {
                "goal_week": 3,
                "goal_day": 0,
                "status": "SHORT5",
                "ends": "W41 2026",
                "code": "B",
                "fact": 2,
                "plan": 7,
            },
        )
        self.assertIsNone(products[0]["economics_7d"]["margin"])
        self.assertIsNone(products[0]["is_new"])


class SourceSyncOrchestrationTests(unittest.TestCase):
    def test_combined_trigger_attempts_both_sources_and_reports_partial_failure(self):
        for failed in (None, "WB", "YM"):
            with (
                self.subTest(failed=failed),
                mock.patch.object(
                    wb,
                    "sync_all",
                    side_effect=wb.SourceDataError("WB error") if failed == "WB" else None,
                    return_value={"ok": True, "saved": 10, "sheet_count": 5},
                ) as wb_sync,
                mock.patch.object(
                    ym,
                    "sync_all",
                    side_effect=wb.SourceDataError("YM error") if failed == "YM" else None,
                    return_value={"ok": True, "saved": 7, "sheet_count": 1},
                ) as ym_sync,
            ):
                report = wb.sync_all_marketplaces()
            wb_sync.assert_called_once_with()
            ym_sync.assert_called_once_with()
            self.assertEqual(report["ok"], failed is None)
            self.assertEqual(report["saved"], 17 if failed is None else 7 if failed == "WB" else 10)
            self.assertIn("YANDEX MARKET", report["marketplaces"])
            if failed:
                self.assertIn(f"{failed} error", report["error"])

    def test_fetch_selects_only_ym_and_wb_selection_is_unchanged(self):
        service = mock.MagicMock()
        api = service.spreadsheets.return_value
        api.get.return_value.execute.return_value = {
            "sheets": [
                {
                    "properties": {
                        "title": title,
                        "sheetId": index,
                        "gridProperties": {"rowCount": 30},
                    }
                }
                for index, title in enumerate(("RIMILI WB", "YM", "TRIS WB", "YM other"))
            ]
        }
        with (
            mock.patch.object(wb.google_service_account, "has_credentials", return_value=True),
            mock.patch.object(wb.google_service_account, "get_credentials", return_value=object()),
            mock.patch("googleapiclient.discovery.build", return_value=service),
        ):
            api.values.return_value.batchGet.return_value.execute.return_value = {
                "valueRanges": [{"values": []}]
            }
            self.assertEqual([s["title"] for s in wb.fetch_sheet_rows("YM")], ["YM"])
            self.assertEqual(api.values.return_value.batchGet.call_args.kwargs["ranges"], ["'YM'!1:30"])
            api.values.return_value.batchGet.return_value.execute.return_value = {
                "valueRanges": [{"values": []}, {"values": []}]
            }
            self.assertEqual([s["title"] for s in wb.fetch_wb_sheet_rows()], ["RIMILI WB", "TRIS WB"])
            self.assertEqual(
                api.values.return_value.batchGet.call_args.kwargs["ranges"],
                ["'RIMILI WB'!A1:X30", "'TRIS WB'!A1:X30"],
            )
