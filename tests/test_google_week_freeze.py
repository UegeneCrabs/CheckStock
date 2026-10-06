"""Local-only safety and value preservation checks for historical week freezing."""

import copy
import importlib
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def literal(value):
    key = (
        "boolValue"
        if isinstance(value, bool)
        else "numberValue"
        if isinstance(value, (int, float))
        else "stringValue"
    )
    return {"userEnteredValue": {key: value}, "effectiveValue": {key: value}, "formattedValue": str(value)}


def formula(expression, value):
    cell = literal(value)
    cell["userEnteredValue"] = {"formulaValue": expression}
    return cell


class FakeGoogle:
    def __init__(self, sheets):
        self.sheets = sheets
        self.grid_reads = 0
        self.before_read = None
        self.before_write = None
        self.fail_after_write = False
        self.requests = []
        self.executions = []

    def spreadsheets(self):
        return self

    def get(self, **kwargs):
        if kwargs.get("includeGridData"):
            self.grid_reads += 1
            if self.before_read:
                self.before_read(self.grid_reads)
        sheets = copy.deepcopy(self.sheets)
        if "ranges" in kwargs:
            sheets = [
                s
                for s in sheets
                if "'" + s["properties"]["title"].replace("'", "''") + "'" in kwargs["ranges"]
            ]
        request = MagicMock()
        request.execute.return_value = {"sheets": sheets}
        return request

    def batchUpdate(self, **kwargs):
        self.requests.append(kwargs)
        if self.before_write:
            self.before_write()

        def execute(**options):
            self.executions.append(options)
            for item in kwargs["body"]["requests"]:
                operation = item["copyPaste"]
                assert operation["source"] == operation["destination"]
                assert operation["pasteType"] == "PASTE_VALUES"
                grid = operation["source"]
                sheet = next(s for s in self.sheets if s["properties"]["sheetId"] == grid["sheetId"])
                for row in sheet["data"][0]["rowData"][grid["startRowIndex"] : grid["endRowIndex"]]:
                    for cell in row.get("values", [])[grid["startColumnIndex"] : grid["endColumnIndex"]]:
                        if cell.get("effectiveValue"):
                            cell["userEnteredValue"] = copy.deepcopy(cell["effectiveValue"])
            if self.fail_after_write:
                raise TimeoutError("Response lost after server accepted copy")
            return {}

        return SimpleNamespace(execute=execute)


class GoogleWeekFreezeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("dotenv.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            cls.freeze = importlib.import_module("app.integrations.google_week_freeze")
            cls.week = importlib.import_module("app.integrations.google_week_update")
            cls.repo = importlib.import_module("app.repositories.google_week_freeze")
            cls.settings_repo = importlib.import_module("app.repositories.google_week_update")

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="checkstock-freeze-test-")
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.settings = replace(
            self.settings_repo.WeekUpdateSettings(), updated_at="2026-09-20T00:00:00+03:00"
        )
        self.state = self.repo.WeekFreezeState(
            spreadsheet_id=self.week.spreadsheet_id(self.settings.spreadsheet_url),
            sheet_ids=(17,),
            selection_initialized=True,
        )
        self.now = datetime.fromisoformat("2026-10-02T12:00:00+03:00")
        self.sheet = {
            "properties": {
                "sheetId": 17,
                "title": "История",
                "sheetType": "GRID",
                "gridProperties": {"rowCount": 1000, "columnCount": 15},
            },
            "data": [
                {
                    "rowData": [
                        {"values": [literal("")] * 7 + [literal(f"W{w} 2026") for w in range(36, 41)]},
                        {"values": [{} for _ in range(15)]},
                    ]
                }
            ],
        }
        self.set_identity_headers(0)
        self.set_cell(1, 7, formula("=10+2", 12))
        self.set_cell(1, 8, formula('="0012"', "0012"))
        self.set_cell(1, 9, formula("=TRUE()", True))
        self.set_cell(1, 10, formula("=99+1", 100))
        self.set_cell(1, 11, formula("=199+1", 200))
        self.google = FakeGoogle([self.sheet])
        patcher = patch.object(
            self.freeze, "app_settings", SimpleNamespace(database_path=self.folder / "db.sqlite")
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def set_cell(self, row, col, cell, *, sheet=None):
        rows = (sheet or self.sheet)["data"][0]["rowData"]
        while len(rows) <= row:
            rows.append({"values": []})
        while len(rows[row]["values"]) <= col:
            rows[row]["values"].append({})
        rows[row]["values"][col] = cell

    def set_identity_headers(self, row, *, sheet=None):
        self.set_cell(row, 0, literal("ARTICLE"), sheet=sheet)
        self.set_cell(row, 1, literal("BARCODE"), sheet=sheet)

    def run_freeze(self, **kwargs):
        return self.freeze.freeze_sheets(
            self.settings, self.state, now=self.now, service=self.google, **kwargs
        )

    def backups(self):
        return list((self.folder / "backups" / "google-week-freeze").glob("*.json"))

    def assert_skipped(self, text):
        report = self.run_freeze()
        self.assertFalse(report["complete"])
        self.assertIn(text, report["sheets"][0]["issue"])
        self.assertEqual(self.google.requests, [])
        self.assertEqual(self.backups(), [])
        return report

    def test_week39_freezes_through38_below_header_and_preserves_types_format_and_current_week(self):
        cell = self.sheet["data"][0]["rowData"][1]["values"][7]
        cell["userEnteredFormat"] = {"numberFormat": {"type": "NUMBER", "pattern": "0.00"}}
        self.set_cell(999, 7, formula("=2+3", 5))
        self.google.before_write = lambda: self.assertEqual(len(self.backups()), 1)
        report = self.run_freeze()
        self.assertTrue(report["complete"])
        self.assertEqual(report["week"], "W39 2026")
        self.assertEqual(report["sheets"][0]["ranges"], ["H2:J1000"])
        self.assertEqual((report["total_formulas"], report["written_cells"]), (4, 4))
        values = self.sheet["data"][0]["rowData"][1]["values"]
        self.assertEqual(values[7]["userEnteredValue"], {"numberValue": 12})
        self.assertEqual(values[8]["userEnteredValue"], {"stringValue": "0012"})
        self.assertEqual(values[9]["userEnteredValue"], {"boolValue": True})
        self.assertIn("formulaValue", values[10]["userEnteredValue"])
        self.assertIn("formulaValue", values[11]["userEnteredValue"])
        self.assertEqual(cell["userEnteredFormat"]["numberFormat"]["pattern"], "0.00")
        saved = json.loads(self.backups()[0].read_text(encoding="utf-8"))
        self.assertEqual(saved["sheets"][0]["cells"][0]["previous"], {"formulaValue": "=10+2"})
        self.assertEqual(saved["sheets"][0]["cells"][-1]["cell"], "H1000")
        self.assertEqual(self.google.executions, [{"num_retries": 0}])

    def test_year_boundary_freezes_week53_of_previous_year_and_excludes_week01(self):
        self.now = datetime.fromisoformat("2027-01-11T00:05:00+03:00")
        for col, value in enumerate(("W51 2026", "W52 2026", "W53 2026", "W01 2027", "W02 2027"), 7):
            self.set_cell(0, col, literal(value))
        report = self.run_freeze()
        self.assertEqual(report["week"], "W01 2027")
        self.assertEqual(report["sheets"][0]["ranges"], ["H2:J1000"])

    def test_exact_anchor_matching_does_not_strip_whitespace_or_change_week_padding(self):
        for value in (" W39 2026", "W39 2026 ", "W39  2026", "w39 2026"):
            with self.subTest(value=value):
                self.set_cell(0, 10, literal(value))
                self.assert_skipped("не найдена")
        self.now = datetime.fromisoformat("2027-01-11T00:05:00+03:00")
        self.set_cell(0, 10, literal("W1 2027"))
        self.assert_skipped("не найдена")

    def test_both_identity_headers_are_required_as_whole_cell_values(self):
        for article, barcode in (
            ("", "BARCODE"),
            ("ARTICLE", ""),
            ("ARTICLE old", "BARCODE"),
            ("ARTICLE", "OTHER BARCODE"),
        ):
            with self.subTest(article=article, barcode=barcode):
                self.set_cell(0, 0, literal(article))
                self.set_cell(0, 1, literal(barcode))
                self.assert_skipped("Не найдена строка заголовков с ARTICLE и BARCODE")

    def test_identity_headers_on_different_rows_do_not_form_a_table_header(self):
        self.set_cell(0, 1, literal(""))
        self.set_cell(3, 1, literal("BARCODE"))
        self.assert_skipped("Не найдена строка заголовков с ARTICLE и BARCODE")

    def test_week_must_be_on_the_same_row_as_both_identity_headers(self):
        self.set_cell(0, 0, literal(""))
        self.set_cell(0, 1, literal(""))
        self.set_identity_headers(3)
        self.assert_skipped("не найдена в строке с ARTICLE и BARCODE")

    def test_identity_headers_use_displayed_whole_values_across_entire_row(self):
        self.set_cell(0, 0, literal(""))
        self.set_cell(0, 1, literal(""))
        self.set_cell(0, 13, formula('=" barcode "', "\tbarcode\u00a0"))
        self.set_cell(0, 14, literal(" article "))
        report = self.run_freeze()
        self.assertTrue(report["complete"])
        self.assertEqual(report["sheets"][0]["ranges"], ["H2:J1000"])
        self.assertEqual(report["written_cells"], 3)

    def test_duplicate_week_blocks_outside_table_headers_do_not_define_ranges(self):
        self.move_header(19)
        for row in (3, 6, 9, 30):
            for col, number in enumerate(range(36, 40), 7):
                self.set_cell(row, col, literal(f"W{number} 2026"))
        self.set_cell(6, 0, literal("ARTICLE"))
        self.set_cell(9, 1, literal("BARCODE"))
        self.set_cell(20, 7, formula("=21", 21))
        before = copy.deepcopy(self.sheet["data"][0]["rowData"][:20])
        preview = self.run_freeze(dry_run=True)
        self.assertEqual(preview["sheets"][0]["ranges"], ["H21:J1000"])
        self.assertEqual(preview["total_formulas"], 1)
        report = self.run_freeze()
        self.assertTrue(report["complete"])
        self.assertEqual(report["sheets"][0]["ranges"], ["H21:J1000"])
        self.assertEqual(report["written_cells"], 1)
        self.assertEqual(self.sheet["data"][0]["rowData"][:20], before)
        backup = json.loads(self.backups()[0].read_text(encoding="utf-8"))
        self.assertEqual([item["cell"] for item in backup["sheets"][0]["cells"]], ["H21"])

    def test_missing_identity_header_skips_only_affected_sheet(self):
        other = copy.deepcopy(self.sheet)
        other["properties"].update(sheetId=18, title="Правильная шапка")
        self.set_cell(0, 1, literal(""))
        self.state = replace(self.state, sheet_ids=(17, 18))
        self.google.sheets.append(other)
        report = self.run_freeze()
        self.assertFalse(report["complete"])
        self.assertIn("ARTICLE и BARCODE", report["sheets"][0]["issue"])
        self.assertEqual(report["sheets"][1]["written_cells"], 3)
        self.assertEqual(
            {item["copyPaste"]["source"]["sheetId"] for item in self.google.requests[0]["body"]["requests"]},
            {18},
        )
        self.assertIn("formulaValue", self.sheet["data"][0]["rowData"][1]["values"][7]["userEnteredValue"])

    def test_anchor_before_column_i_is_not_used(self):
        self.set_cell(0, 7, literal("W39 2026"))
        self.set_cell(0, 10, literal(""))
        self.assert_skipped("начиная со столбца I")

    def test_walk_stops_at_first_nonweek_and_does_not_include_earlier_separate_block(self):
        self.set_cell(0, 8, literal("Итого"))
        report = self.run_freeze()
        self.assertEqual(report["sheets"][0]["ranges"], ["J2:J1000"])
        self.assertIn("formulaValue", self.sheet["data"][0]["rowData"][1]["values"][7]["userEnteredValue"])

    def test_current_or_future_week_to_left_is_rejected(self):
        self.set_cell(0, 8, literal("W41 2026"))
        self.assert_skipped("порядок недель")

    def test_future_header_in_data_rows_prevents_freeze(self):
        self.set_cell(25, 8, literal("W40 2026"))
        self.assert_skipped("затрагивает W40")

    def test_repeated_headers_split_ranges_and_preserve_every_header_formula(self):
        self.set_identity_headers(10)
        for col in range(7, 11):
            label = self.sheet["data"][0]["rowData"][0]["values"][col]["formattedValue"]
            self.set_cell(10, col, formula(f'="{label}"', label))
        self.set_cell(11, 7, formula("=21+1", 22))
        report = self.run_freeze()
        self.assertTrue(report["complete"])
        self.assertEqual(report["sheets"][0]["ranges"], ["H2:J10", "H12:J1000"])
        self.assertEqual(report["written_cells"], 4)
        self.assertEqual(len(self.google.requests[0]["body"]["requests"]), 2)
        for cell in self.sheet["data"][0]["rowData"][10]["values"][7:11]:
            self.assertIn("formulaValue", cell["userEnteredValue"])

    def test_overlapping_different_spans_are_rejected(self):
        self.set_identity_headers(10)
        for col, label in ((8, "W37 2026"), (9, "W38 2026"), (10, "W39 2026")):
            self.set_cell(10, col, literal(label))
        self.assert_skipped("пересекающиеся")

    def test_same_span_with_inconsistent_weeks_is_rejected(self):
        self.set_identity_headers(10)
        for col, label in enumerate(("W35 2026", "W37 2026", "W38 2026", "W39 2026"), 7):
            self.set_cell(10, col, literal(label))
        self.assert_skipped("не совпадают")

    def test_disjoint_blocks_are_copied_in_one_atomic_batch(self):
        for col, label in ((12, "W38 2026"), (13, "W39 2026")):
            self.set_cell(0, col, literal(label))
        self.set_cell(0, 11, literal("Итого"))
        self.set_cell(1, 12, formula("=2", 2))
        report = self.run_freeze()
        self.assertEqual(report["sheets"][0]["ranges"], ["H2:J1000", "M2:M1000"])
        self.assertEqual(len(self.google.requests), 1)
        self.assertEqual(len(self.google.requests[0]["body"]["requests"]), 2)

    def test_source_cells_are_never_frozen(self):
        self.settings = replace(self.settings, sheet_name="История", cells=("H50",))
        self.assert_skipped("исходную ячейку")

    def test_cross_boundary_merges_and_protections_are_rejected(self):
        self.sheet["merges"] = [
            {"startColumnIndex": 9, "endColumnIndex": 11, "startRowIndex": 6, "endRowIndex": 7}
        ]
        self.assert_skipped("Объединённые")
        self.sheet.pop("merges")
        self.sheet["protectedRanges"] = [
            {"range": {"sheetId": 17, "startColumnIndex": 8, "endColumnIndex": 9}}
        ]
        self.assert_skipped("защищённые")

    def test_merges_fully_inside_range_remain_unchanged(self):
        merge = {"startColumnIndex": 7, "endColumnIndex": 9, "startRowIndex": 6, "endRowIndex": 7}
        self.sheet["merges"] = [merge]
        self.assertTrue(self.run_freeze()["complete"])
        self.assertEqual(self.sheet["merges"], [merge])

    def test_errors_and_uncomputed_formulas_are_rejected(self):
        self.set_cell(
            1,
            7,
            {
                "userEnteredValue": {"formulaValue": "=1/0"},
                "effectiveValue": {"errorValue": {"type": "DIVIDE_BY_ZERO"}},
            },
        )
        self.assert_skipped("ошибка вычисления")
        self.set_cell(1, 7, {"userEnteredValue": {"formulaValue": "=IMPORTRANGE()"}})
        self.assert_skipped("ещё не рассчитана")

    def test_spill_output_inside_or_outside_range_prevents_destroying_arrays(self):
        for col in (8, 13):
            with self.subTest(col=col):
                self.set_cell(10, col, {"effectiveValue": {"numberValue": 8}})
                self.assert_skipped("формулы массива")
                self.set_cell(10, col, {})

    def test_unrelated_article_array_outputs_to_left_of_targets_are_allowed(self):
        self.set_cell(5, 0, formula("=ARRAYFORMULA(B6:B50)", "ARTICLE"))
        self.set_cell(6, 0, {"effectiveValue": {"numberValue": 123}})
        self.assertTrue(self.run_freeze()["complete"])
        self.assertEqual(
            self.sheet["data"][0]["rowData"][6]["values"][0], {"effectiveValue": {"numberValue": 123}}
        )

    def test_spill_only_target_is_not_reported_as_already_frozen(self):
        for col in (7, 8, 9):
            self.set_cell(1, col, {"effectiveValue": {"numberValue": 12}})
        self.set_cell(1, 6, formula("=SEQUENCE(1,4)", 1))
        self.assert_skipped("формулы массива")

    def test_editable_and_warning_only_protections_do_not_block(self):
        self.sheet["protectedRanges"] = [
            {"range": {"sheetId": 17}, "warningOnly": True},
            {"range": {"sheetId": 17}, "requestingUserCanEdit": True},
        ]
        self.assertTrue(self.run_freeze()["complete"])

    def test_sheet_protection_unprotected_ranges_must_cover_entire_target(self):
        self.sheet["protectedRanges"] = [
            {
                "range": {"sheetId": 17},
                "requestingUserCanEdit": False,
                "unprotectedRanges": [
                    {
                        "sheetId": 17,
                        "startColumnIndex": 7,
                        "endColumnIndex": 10,
                        "startRowIndex": 0,
                        "endRowIndex": 500,
                    },
                    {
                        "sheetId": 17,
                        "startColumnIndex": 7,
                        "endColumnIndex": 10,
                        "startRowIndex": 501,
                        "endRowIndex": 1000,
                    },
                ],
            }
        ]
        self.assert_skipped("защищённые")
        self.sheet["protectedRanges"][0]["unprotectedRanges"][1]["startRowIndex"] = 500
        self.assertTrue(self.run_freeze()["complete"])

    def test_preview_network_error_is_readable_and_does_not_record_an_attempt(self):
        with (
            patch.object(self.settings_repo, "get_settings", return_value=self.settings),
            patch.object(self.repo, "get_state", return_value=self.state),
            patch.object(self.repo, "record_attempt") as attempt,
            patch.object(self.freeze, "freeze_sheets", side_effect=TimeoutError("private request details")),
            self.assertRaisesRegex(ValueError, "проверку недель.*TimeoutError"),
        ):
            self.freeze.preview_now(self.now)
        attempt.assert_not_called()

    def test_blank_string_zero_and_false_formula_results_are_valid(self):
        for col, result in enumerate(("", 0, False), 7):
            self.set_cell(1, col, formula("=IF(TRUE,0,0)", result))
        self.assertEqual(self.run_freeze()["written_cells"], 3)

    def test_renamed_sheet_keeps_id_and_removed_sheet_is_partial_result(self):
        self.sheet["properties"]["title"] = "Переименованный лист"
        self.state = replace(self.state, sheet_ids=(17, 44))
        report = self.run_freeze()
        self.assertEqual(report["sheets"][0]["sheet_name"], "Переименованный лист")
        self.assertFalse(report["complete"])
        self.assertIn("удалён", report["sheets"][1]["issue"])
        self.assertEqual(report["written_cells"], 3)

    def test_selection_is_bound_to_spreadsheet_and_empty_selection_rejected_before_read(self):
        for state in (
            replace(self.state, spreadsheet_id="other"),
            replace(self.state, selection_initialized=False),
            replace(self.state, sheet_ids=()),
        ):
            with self.subTest(state=state), self.assertRaises(ValueError):
                self.freeze.freeze_sheets(self.settings, state, now=self.now, service=self.google)
        self.assertEqual(self.google.grid_reads, 0)

    def test_dry_run_is_read_only_without_backups_or_state_changes(self):
        with (
            patch.object(self.repo, "record_attempt") as attempt,
            patch.object(self.repo, "record_result") as result,
        ):
            report = self.run_freeze(dry_run=True)
        self.assertTrue(report["dry_run"])
        self.assertEqual((report["total_formulas"], report["written_cells"]), (3, 0))
        self.assertEqual(self.google.grid_reads, 1)
        self.assertEqual(self.google.requests, [])
        self.assertEqual(self.backups(), [])
        attempt.assert_not_called()
        result.assert_not_called()

    def test_revalidation_detects_changed_formula_even_with_same_displayed_result(self):
        def mutate(read):
            if read == 2:
                self.set_cell(1, 7, formula("=6+6", 12))

        self.google.before_read = mutate
        self.assert_skipped("изменились")

    def test_revalidation_rejects_removed_identity_header(self):
        def mutate(read):
            if read == 2:
                self.set_cell(0, 1, literal(""))

        self.google.before_read = mutate
        self.assert_skipped("изменились")

    def test_revalidation_detects_new_protection_or_future_header(self):
        def mutate(read):
            if read == 2:
                self.set_cell(15, 8, literal("W41 2026"))

        self.google.before_read = mutate
        self.assert_skipped("изменились")

    def test_backup_failure_blocks_google_write(self):
        with (
            patch.object(self.freeze, "_save_backup", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            self.run_freeze()
        self.assertEqual(self.google.requests, [])

    def test_repeated_success_and_lost_response_retry_do_not_rewrite_constants(self):
        self.google.fail_after_write = True
        with self.assertRaises(TimeoutError):
            self.run_freeze()
        self.assertEqual(len(self.backups()), 1)
        self.google.fail_after_write = False
        report = self.run_freeze()
        self.assertTrue(report["complete"])
        self.assertEqual((report["total_formulas"], report["written_cells"]), (0, 0))
        self.assertEqual(len(self.google.requests), 1)
        self.assertEqual(len(self.backups()), 1)

    def test_readonly_client_is_used_until_backup_ready(self):
        with patch.object(self.week, "_google_service", return_value=self.google) as factory:
            self.freeze.freeze_sheets(self.settings, self.state, now=self.now, dry_run=True)
            factory.assert_called_once_with(read_only=True)
            factory.reset_mock()
            self.freeze.freeze_sheets(self.settings, self.state, now=self.now)
        self.assertEqual(factory.call_args_list[0].kwargs, {"read_only": True})
        self.assertEqual(factory.call_args_list[1].kwargs, {})

    def move_header(self, row, *, sheet=None):
        sheet = sheet if sheet is not None else self.sheet
        header = copy.deepcopy(sheet["data"][0]["rowData"][0])
        sheet["data"][0]["rowData"][0] = {"values": []}
        for col, cell in enumerate(header["values"]):
            self.set_cell(row, col, cell, sheet=sheet)

    def test_row20_header_starts_at21_and_excludes_header_and_above_from_backup(self):
        self.move_header(19)
        for col, label in enumerate(("W36 2026", "W37 2026", "W38 2026", "W39 2026"), 7):
            self.set_cell(19, col, formula(f'="{label}"', label))
        self.set_cell(20, 7, formula("=3*7", 21))
        self.set_cell(999, 9, formula("=3*9", 27))
        before = copy.deepcopy(self.sheet["data"][0]["rowData"][:20])
        preview = self.run_freeze(dry_run=True)
        self.assertEqual(preview["sheets"][0]["ranges"], ["H21:J1000"])
        self.assertEqual(preview["total_formulas"], 2)
        self.assertEqual(self.backups(), [])
        report = self.run_freeze()
        self.assertTrue(report["complete"])
        self.assertEqual(report["written_cells"], 2)
        self.assertEqual(self.sheet["data"][0]["rowData"][:20], before)
        grid = self.google.requests[0]["body"]["requests"][0]["copyPaste"]["source"]
        self.assertEqual(grid["startRowIndex"], 20)
        backup = json.loads(self.backups()[0].read_text(encoding="utf-8"))
        self.assertEqual([cell["cell"] for cell in backup["sheets"][0]["cells"]], ["H21", "J1000"])

    def test_every_sheet_discovers_its_own_header_row(self):
        other = copy.deepcopy(self.sheet)
        other["properties"].update(sheetId=18, title="Другой лист")
        self.move_header(19)
        self.move_header(6, sheet=other)
        self.set_cell(20, 7, formula("=21", 21))
        self.set_cell(7, 7, formula("=8", 8), sheet=other)
        self.state = replace(self.state, sheet_ids=(17, 18))
        self.google.sheets.append(other)
        report = self.run_freeze()
        self.assertTrue(report["complete"])
        self.assertEqual([item["ranges"] for item in report["sheets"]], [["H21:J1000"], ["H8:J1000"]])
        self.assertEqual(report["written_cells"], 2)
        for sheet in (self.sheet, other):
            self.assertIn("formulaValue", sheet["data"][0]["rowData"][1]["values"][7]["userEnteredValue"])

    def test_side_by_side_blocks_use_independent_start_rows(self):
        self.set_identity_headers(19)
        self.set_cell(19, 12, formula('="W38 2026"', "W38 2026"))
        self.set_cell(19, 13, literal("W39 2026"))
        self.set_cell(1, 12, formula("=10", 10))
        self.set_cell(20, 12, formula("=20", 20))
        report = self.run_freeze()
        self.assertTrue(report["complete"])
        self.assertEqual(report["sheets"][0]["ranges"], ["H2:J1000", "M21:M1000"])
        self.assertEqual(report["written_cells"], 4)
        for row in (1, 19):
            self.assertIn(
                "formulaValue", self.sheet["data"][0]["rowData"][row]["values"][12]["userEnteredValue"]
            )

    def test_guards_ignore_untouched_header_and_rows_above(self):
        self.move_header(19)
        self.settings = replace(self.settings, sheet_name="История", cells=("H4",))
        self.set_cell(5, 8, {"effectiveValue": {"errorValue": {"type": "DIVIDE_BY_ZERO"}}})
        self.set_cell(7, 8, {"effectiveValue": {"numberValue": 4}})
        self.set_cell(9, 9, literal("W41 2026"))
        self.set_cell(20, 7, formula("=21", 21))
        self.sheet["merges"] = [
            {"startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 6, "endColumnIndex": 11}
        ]
        self.sheet["protectedRanges"] = [{"range": {"sheetId": 17, "startRowIndex": 0, "endRowIndex": 20}}]
        before = copy.deepcopy(self.sheet["data"][0]["rowData"][:20])
        self.assertTrue(self.run_freeze()["complete"])
        self.assertEqual(self.sheet["data"][0]["rowData"][:20], before)

    def test_merge_crossing_header_data_boundary_is_rejected(self):
        self.move_header(19)
        self.set_cell(20, 7, formula("=21", 21))
        self.sheet["merges"] = [
            {"startRowIndex": 19, "endRowIndex": 21, "startColumnIndex": 7, "endColumnIndex": 8}
        ]
        self.assert_skipped("Объединённые")

    def test_unprotected_exception_only_needs_to_cover_data_rows(self):
        self.move_header(19)
        self.set_cell(20, 7, formula("=21", 21))
        self.sheet["protectedRanges"] = [
            {
                "range": {"sheetId": 17},
                "unprotectedRanges": [
                    {
                        "sheetId": 17,
                        "startRowIndex": 20,
                        "endRowIndex": 1000,
                        "startColumnIndex": 7,
                        "endColumnIndex": 10,
                    }
                ],
            }
        ]
        self.assertTrue(self.run_freeze()["complete"])

    def test_moving_header_during_revalidation_prevents_write(self):
        self.move_header(19)
        self.set_cell(21, 7, formula("=22", 22))

        def mutate(read):
            if read == 2:
                rows = self.sheet["data"][0]["rowData"]
                rows[20], rows[19] = rows[19], {"values": []}

        self.google.before_read = mutate
        self.assert_skipped("изменились")

    def test_header_on_last_row_never_copies_header_or_anything_above(self):
        self.move_header(999)
        report = self.run_freeze()
        self.assertTrue(report["complete"])
        self.assertEqual(report["sheets"][0]["ranges"], [])
        self.assertEqual(report["written_cells"], 0)
        self.assertEqual(self.google.requests, [])
        self.assertEqual(self.backups(), [])


if __name__ == "__main__":
    unittest.main()
