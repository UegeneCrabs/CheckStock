"""Transition runner: refresh official reports, then copy facts into RBE's isolated DB.

The importer intentionally invokes the existing, production-tested Yandex
report client while RBE is being split into its own repository.  Data is then
copied into RBE's separate SQLite database; the dashboard never reads the
main application's database at runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
ROOT = SITE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SITE))

from app.yandex import financial_reports  # noqa: E402
from import_from_checkstock import main as copy_into_rbe  # noqa: E402

STORES = ("rimili", "tris", "rockkiddo", "trusthome", "sokoloff", "gogol")


def run(date_from: date, date_to: date) -> int:
    results: list[dict] = []
    for store_slug in STORES:
        try:
            report = financial_reports.import_period(store_slug, date_from, date_to)
            results.append({"store": store_slug, "ok": True, "report": report})
        except Exception as error:  # one cabinet failure must not skip the others
            results.append({"store": store_slug, "ok": False, "error": str(error)})
        # Windows service logs can use a legacy code page; keep the worker
        # alive even if an API error contains Cyrillic text.
        print(json.dumps(results[-1], ensure_ascii=True), flush=True)
    copy_into_rbe(str(ROOT / "data" / "checkstock.db"))
    print(json.dumps({"copied_to_rbe": True, "successful": sum(item["ok"] for item in results)}, ensure_ascii=True))
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date-from", required=True, type=date.fromisoformat)
    parser.add_argument("--date-to", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    if args.date_to < args.date_from:
        parser.error("Дата окончания раньше даты начала")
    raise SystemExit(run(args.date_from, args.date_to))
