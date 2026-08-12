import unittest

from app import unit_costs


class UnitCostTests(unittest.TestCase):
    def test_cost_sheet_is_normalized_and_deduplicated(self) -> None:
        rows = [
            ["Отчёт"],
            ["АртикулВБ", "Себес, руб", "Проч. затр, руб"],
            [" 1001.0 ", "1 234,50", "10"],
            ["1001", "1200", "20"],
            ["1002", "—", "5"],
        ]

        result = unit_costs.parse_cost_rows(rows)

        self.assertEqual(
            result,
            [{"article": "1001", "purchase_price": 1200.0, "other_cost": 20.0}],
        )

    def test_missing_headers_are_rejected(self) -> None:
        with self.assertRaisesRegex(unit_costs.UnitCostSyncError, "не найдены колонки"):
            unit_costs.parse_cost_rows([["Артикул", "Цена"]])


if __name__ == "__main__":
    unittest.main()
