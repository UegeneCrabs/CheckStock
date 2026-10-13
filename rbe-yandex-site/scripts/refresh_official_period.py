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
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
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
        # RBE uses these orders only for the creation-date funnel. Delivered
        # items and returns come from the independent financial reports.
        return {"store": store_slug, "ok": True, "report": financial_reports.import_period(store_slug, date_from, date_to, orders_lookback_days=0)}
    except Exception as error:  # a temporary API error must not stop other cabinets
        return {"store": store_slug, "ok": False, "error": str(error)}


def run(date_from: date, date_to: date, stores: tuple[str, ...] = STORES, workers: int = 1) -> int:
    if date_from < datetime.now(ZoneInfo('Europe/Moscow')).date() - timedelta(days=90):
        raise ValueError('Даты старше 90 дней не запрашиваются. Сохранённые данные остаются доступны.')
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = {executor.submit(_import_store, store_slug, date_from, date_to): store_slug for store_slug in stores}
        for future in as_completed(futures):
            results.append(future.result())
            # Windows service logs can use a legacy code page; keep the worker
            # alive even if an API error contains Cyrillic text.
            print(json.dumps(results[-1], ensure_ascii=True), flush=True)
    successful = tuple(item['store'] for item in results if item['ok'])
    # A failed API call must not masquerade as a fresh update of cached facts.
    if successful:
        dates = [date_from, date_to]
        for item in results:
            if item['ok']:
                for account in item.get('report', {}).get('reports', []):
                    dates.extend(date.fromisoformat(d) for d in account.get('reconciled_dates', []))
        copy_into_rbe(str(ROOT / "data" / "checkstock.db"), stores=successful, date_from=min(dates), date_to=max(dates))
    print(json.dumps({"copied_to_rbe": bool(successful), "successful": len(successful)}, ensure_ascii=True))
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date-from", required=True, type=date.fromisoformat)
    parser.add_argument("--date-to", required=True, type=date.fromisoformat)
    parser.add_argument("--stores", nargs="+", choices=STORES, default=STORES)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if args.date_to < args.date_from:
        parser.error("Дата окончания раньше даты начала")
    raise SystemExit(run(args.date_from, args.date_to, tuple(args.stores), args.workers))
