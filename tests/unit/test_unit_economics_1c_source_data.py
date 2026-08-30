import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import db
from app import unit_economics_1c_source_data as source_data
from app.dto.unit_economics_1c import UnitEconomics1CCabinetSettingsRequest
from app.repositories import core

NOW = "2026-08-19T08:00:00+00:00"


def _sheet(title: str, sheet_id: int, rows: list[list[object]]) -> dict:
    header = [""] * 24
    header[1] = "АртикулВБ"
    header[7] = "Тег"
    header[8] = "Менеджер"
    header[10] = "Артикул поставщика внешний"
    header[19] = "Себес, руб"
    header[20] = "Проч.затр, руб"
    header[23] = "Процент для учета маркетинговых затрат"
    return {"title": title, "sheet_id": sheet_id, "rows": [["Группа"], header, *rows]}


def _row(
    article: str,
    *,
    tag: str,
    external: str,
    purchase: object,
    fulfillment: object,
    commission: object,
    manager: str = "",
) -> list[object]:
    row = [""] * 24
    row[1] = article
    row[7] = tag
    row[8] = manager
    row[10] = external
    row[19] = purchase
    row[20] = fulfillment
    row[23] = commission
    return row


class UnitEconomics1CSourceDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = mock.patch.object(
            core,
            "DB_PATH",
            Path(self.temp.name) / "source-data.sqlite3",
        )
        self.path_patch.start()
        db.init_db()
        db.replace_catalog(
            "rimili",
            "WB",
            [
                {"article": "111", "barcode": "1001", "name": "RIMILI товар"},
                {"article": "112", "barcode": "1003", "name": "RIMILI товар 2"},
            ],
            NOW,
        )
        db.replace_catalog(
            "trusthome",
            "WB",
            [{"article": "222 / XL", "barcode": "1002", "name": "TRUSTHOME товар"}],
            NOW,
        )

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temp.cleanup()

    def test_sync_matches_combined_sheet_and_replaces_current_snapshot(self) -> None:
        db.save_unit_economics_1c_cabinet_settings(
            "rimili",
            UnitEconomics1CCabinetSettingsRequest(acquiring_percent=4.2),
            updated_at=NOW,
            updated_by_user_id=7,
            updated_by_name="Тест",
        )
        sheets = [
            _sheet(
                "RIMILI WB",
                10,
                [
                    _row(
                        "111",
                        tag="1 / 0 / SHORT1 / W34 2026",
                        external="B / Ф:4 / П:7",
                        purchase=" 901,35",
                        fulfillment="100,53",
                        commission="4,00",
                        manager="Анастасия Кипке",
                    )
                ],
            ),
            _sheet(
                "SOKOLOFF и TRUSTHOME WB",
                11,
                [
                    _row(
                        "222",
                        tag="15 / 2 / OVER / W47 2026",
                        external="A / Ф:15 / П:5",
                        purchase="1200",
                        fulfillment="80",
                        commission="5",
                    )
                ],
            ),
        ]

        report = source_data.sync_all(sheets)

        self.assertEqual(report["saved"], 2)
        self.assertEqual(report["unmatched"], 0)
        values = {
            (row["store_slug"], row["article"]): row
            for row in db.get_unit_economics_1c_product_reference_rows(("rimili", "trusthome"))
        }
        rimili = values[("rimili", "111")]
        self.assertEqual(rimili["purchase_price"], 901.35)
        self.assertEqual(rimili["fulfillment_cost"], 100.53)
        self.assertEqual(rimili["team_commission_percent"], 4)
        self.assertEqual(rimili["manager"], "Анастасия Кипке")
        self.assertEqual(rimili["goal_week"], 1)
        self.assertEqual(rimili["stock_status"], "SHORT1")
        self.assertEqual(rimili["stock_end_week"], "W34 2026")
        self.assertEqual(rimili["abc_code"], "B")
        self.assertEqual(rimili["turnover_days"], 28)
        self.assertEqual(rimili["fact_sales"], 4)
        self.assertEqual(rimili["plan_sales"], 7)
        self.assertEqual(values[("trusthome", "222 / XL")]["abc_code"], "A")
        cabinet = db.get_unit_economics_1c_cabinet_settings("rimili")
        self.assertEqual(cabinet.team_commission_percent, 4)
        self.assertEqual(cabinet.acquiring_percent, 4.2)
        self.assertEqual(cabinet.updated_by_name, "Google Sheets")

        source_data.sync_all(
            [
                _sheet(
                    "RIMILI WB",
                    10,
                    [
                        _row(
                            "111",
                            tag="2 / 1 / OVER / W40 2026",
                            external="C / Ф:6 / П:8",
                            purchase="950",
                            fulfillment="110",
                            commission="4",
                        )
                    ],
                )
            ]
        )
        conn = core.get_connection()
        count = conn.execute("SELECT COUNT(*) FROM unit_economics_1c_source_values").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)
        refreshed = {
            (row["store_slug"], row["article"]): row
            for row in db.get_unit_economics_1c_product_reference_rows(("rimili", "trusthome"))
        }
        self.assertEqual(refreshed[("rimili", "111")]["purchase_price"], 950)
        self.assertEqual(refreshed[("rimili", "111")]["abc_code"], "C")
        self.assertIsNone(refreshed[("trusthome", "222 / XL")]["purchase_price"])

    def test_product_commissions_are_preserved_and_cabinet_uses_most_common_value(self) -> None:
        sheets = [
            _sheet(
                "RIMILI WB",
                10,
                [
                    _row(
                        "112",
                        tag="0 / 0 / SHORT1",
                        external="D / Ф:0 / П:0",
                        purchase="1",
                        fulfillment="2",
                        commission="4",
                    ),
                    _row(
                        "111",
                        tag="0 / 0 / SHORT1",
                        external="D / Ф:0 / П:0",
                        purchase="1",
                        fulfillment="2",
                        commission="5",
                    ),
                ],
            )
        ]

        report = source_data.sync_all(sheets)

        conn = core.get_connection()
        count = conn.execute("SELECT COUNT(*) FROM unit_economics_1c_source_values").fetchone()[0]
        conn.close()
        self.assertEqual(count, 2)
        self.assertEqual(report["commission_conflicts"], {"rimili": {4.0: 1, 5.0: 1}})
        commissions = {
            row["article"]: row["team_commission_percent"]
            for row in db.get_unit_economics_1c_product_reference_rows(("rimili",))
        }
        self.assertEqual(commissions["111"], 5)
        self.assertEqual(commissions["112"], 4)
        self.assertEqual(db.get_unit_economics_1c_cabinet_settings("rimili").team_commission_percent, 4)

    def test_schema_migrates_an_earlier_source_snapshot_table(self) -> None:
        conn = core.get_connection()
        conn.execute("ALTER TABLE unit_economics_1c_source_values DROP COLUMN team_commission_percent")
        conn.execute("ALTER TABLE unit_economics_1c_source_values DROP COLUMN manager")
        conn.commit()
        conn.close()

        db.init_db()

        conn = core.get_connection()
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(unit_economics_1c_source_values)").fetchall()
        }
        conn.close()
        self.assertIn("team_commission_percent", columns)
        self.assertIn("manager", columns)


if __name__ == "__main__":
    unittest.main()
