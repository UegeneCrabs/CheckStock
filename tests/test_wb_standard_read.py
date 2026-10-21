import unittest
from unittest.mock import MagicMock, patch

from test_wb_standard_costs import fixture

from app.dto.wb_standard_costs import Snapshot
from app.repositories import wb_standard_costs as repository
from app.web.routers.wb_standard_costs import load_reports


class ReadTests(unittest.TestCase):
    def test_failed_store_does_not_hide_healthy_store(self):
        with (
            patch.object(
                repository, "load", side_effect=[ValueError("private diagnostic"), Snapshot(rows=[])]
            ),
            patch("app.web.routers.wb_standard_costs.enrich", return_value=Snapshot(rows=[])),
            self.assertLogs("app.web.routers.wb_standard_costs", level="ERROR"),
        ):
            result = load_reports(("tris", "rimili"))["stores"]
        self.assertIsNone(result[0]["report"])
        self.assertIsNotNone(result[0].error)
        self.assertNotIn("private diagnostic", result[0]["error"])
        self.assertEqual(result[1]["report"], Snapshot(rows=[]))
        self.assertIsNone(result[1].error)

    def test_old_snapshot_rebuilt_without_writes(self):
        import json

        conn = MagicMock()
        conn.execute.return_value.fetchone.side_effect = [
            {"payload": json.dumps({"version": 5})},
            {"payload": json.dumps(fixture())},
        ]
        with patch.object(repository, "get_connection") as connection:
            connection.return_value.__enter__.return_value = conn
            result = repository.load("tris")
        self.assertEqual(result["version"], 6)
        self.assertEqual(len(result["rows"]), 1)
        self.assertTrue(all(call.args[0].startswith("SELECT") for call in conn.execute.call_args_list))
        conn.commit.assert_not_called()

    def test_missing_old_sources_report_error(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.side_effect = [{"payload": '{"version":3}'}, None]
        with patch.object(repository, "get_connection") as connection:
            connection.return_value.__enter__.return_value = conn
            with self.assertRaises(ValueError):
                repository.load("tris")
