import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from app import db
from app import unit_economics_1c_reference_data as reference_data
from app.repositories import core

NOW = "2026-08-19T08:00:00+00:00"


class UnitEconomics1CReferenceDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = mock.patch.object(
            core,
            "DB_PATH",
            Path(self.temp.name) / "reference-data.sqlite3",
        )
        self.path_patch.start()
        db.init_db()
        db.replace_catalog(
            "rimili",
            "WB",
            [
                {"article": "367080326", "barcode": "2040214706624", "name": "Первый"},
                {"article": "340331510", "barcode": "2040214706625", "name": "Второй"},
                {"article": "371727738", "barcode": "2040214706626", "name": "Лишний в БД"},
            ],
            NOW,
        )

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temp.cleanup()

    def _references(self) -> dict[str, dict]:
        return {
            str(row["article"]): row for row in db.get_unit_economics_1c_product_reference_rows(("rimili",))
        }

    def test_classifications_match_either_sheet_identifier_and_default_to_none(self) -> None:
        report = reference_data.sync_product_classifications(
            [
                ["Артикул", "Баркод", "Код"],
                ["2040214706624", "", "A"],
                ["", "340331510", "B"],
                ["sheet-only", "999999999", "C"],
            ]
        )

        references = self._references()
        self.assertEqual(references["367080326"]["abc_code"], "A")
        self.assertEqual(references["367080326"]["turnover_days"], 30)
        self.assertEqual(references["340331510"]["abc_code"], "B")
        self.assertEqual(references["340331510"]["turnover_days"], 28)
        self.assertIsNone(references["371727738"]["abc_code"])
        self.assertEqual(references["371727738"]["turnover_days"], 21)
        self.assertEqual(report["matched"], 2)
        self.assertEqual(report["code_none"], 1)
        self.assertEqual(report["sheet_rows_without_catalog"], 1)

    def test_commission_and_weekly_wb_category_are_joined(self) -> None:
        reference_data.sync_wb_commissions(
            [
                ["Категория", "Комиссия WB, %"],
                ["Смесители", "24,62"],
                ["Полотенца", "18.5"],
            ]
        )
        with (
            mock.patch.object(reference_data.wb_tokens, "has_token", return_value=True),
            mock.patch.object(reference_data.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(
                reference_data.wb_api,
                "get_cards_list",
                return_value=[
                    {
                        "nmID": 367080326,
                        "imtID": 445566,
                        "createdAt": "2026-08-01T10:15:00Z",
                        "subjectID": 1708,
                        "subjectName": " смесители ",
                    }
                ],
            ) as get_cards,
        ):
            report = reference_data.sync_product_categories("rimili")

        references = self._references()
        self.assertEqual(report["matched"], 1)
        self.assertEqual(references["367080326"]["category"], "смесители")
        self.assertEqual(references["367080326"]["imt_id"], 445566)
        self.assertEqual(references["367080326"]["card_created_at"], "2026-08-01T10:15:00Z")
        self.assertEqual(references["367080326"]["subject_commission_percent"], 24.62)
        self.assertIsNone(references["340331510"]["category"])
        get_cards.assert_called_once_with("token")
        threshold = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        self.assertFalse(db.unit_economics_1c_product_categories_due("rimili", threshold))
        self.assertFalse(db.unit_economics_1c_wb_commissions_due(threshold))

    def test_reference_rows_are_deleted_with_catalog_product(self) -> None:
        reference_data.sync_product_classifications([["Артикул", "Баркод", "Код"], ["", "367080326", "A"]])
        db.replace_catalog(
            "rimili",
            "WB",
            [
                {"article": "340331510", "barcode": "2040214706625", "name": "Второй"},
                {"article": "371727738", "barcode": "2040214706626", "name": "Третий"},
            ],
            NOW,
        )

        conn = core.get_connection()
        count = conn.execute("SELECT COUNT(*) FROM unit_economics_1c_product_classifications").fetchone()[0]
        conn.close()
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
