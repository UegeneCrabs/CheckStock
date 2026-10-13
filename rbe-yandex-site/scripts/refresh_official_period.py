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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
ROOT = SITE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SITE))

from app.yandex import financial_reports  # noqa: E402
from import_from_checkstock import main as copy_into_rbe  # noqa: E402

STORES = ("rimili", "tris", "rockkiddo", "trusthome", "sokoloff", "gogol")


def _import_store(store_slug: str, date_from: date, date_to: date) -> dict:
    """Import one cabinet; reports inside a cabinet stay sequential.

    Market allows only one active report for a single API key.  Parallelism is
    therefore deliberately applied between cabinets, not between report types
    within one cabinet.
    """

    try:
        return {"store": store_slug, "ok": True, "report": financial_reports.import_period(store_slug, date_from, date_to)}
    except Exception as error:  # a temporary API error must not stop other cabinets
        return {"store": store_slug, "ok": False, "error": str(error)}


def run(date_from: date, date_to: date, stores: tuple[str, ...] = STORES, workers: int = 6) -> int:
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(max(1, workers), len(stores))) as executor:
        futures = {executor.submit(_import_store, store_slug, date_from, date_to): store_slug for store_slug in stores}
        for future in as_completed(futures):
            results.append(future.result())
            # Windows service logs can use a legacy code page; keep the worker
            # alive even if an API error contains Cyrillic text.
            print(json.dumps(results[-1], ensure_ascii=True), flush=True)
    successful = tuple(item['store'] for item in results if item['ok'])
    # A failed API call must not masquerade as a fresh update of cached facts.
    if successful:
        copy_into_rbe(str(ROOT / "data" / "checkstock.db"), stores=successful, date_from=date_from, date_to=date_to)
    print(json.dumps({"copied_to_rbe": bool(successful), "successful": len(successful)}, ensure_ascii=True))
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date-from", required=True, type=date.fromisoformat)
    parser.add_argument("--date-to", required=True, type=date.fromisoformat)
    parser.add_argument("--stores", nargs="+", choices=STORES, default=STORES)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    if args.date_to < args.date_from:
        parser.error("Дата окончания раньше даты начала")
    raise SystemExit(run(args.date_from, args.date_to, tuple(args.stores), args.workers))
