from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import sales


def _sync_store(
    store_slug: str,
    days: int,
    period: tuple[date, date] | None,
) -> tuple[str, dict]:
    if period is None:
        print(f"[{store_slug}] WB orders: start, days={days}", flush=True)
        result = sales.sync_store(store_slug, "WB", days)
    else:
        start, end = period
        print(f"[{store_slug}] WB orders: start, period={start}..{end - timedelta(days=1)}", flush=True)
        result = sales.sync_store_period(store_slug, "WB", start, end)
    state = "ok" if result.get("ok") else "error"
    print(f"[{store_slug}] WB orders: {state}, rows={result.get('rows', 0)}", flush=True)
    return store_slug, result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill WB FBO/FBS orders and FBS supplier/WB statuses in parallel by store."
    )
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--store", action="append", dest="stores")
    parser.add_argument("--date-from")
    parser.add_argument("--date-to", help="Inclusive end date")
    args = parser.parse_args()
    days = max(int(args.days), 1)
    configured_stores = sales._configured_stores("WB")
    stores = list(dict.fromkeys(args.stores or configured_stores))
    unknown_stores = sorted(set(stores) - set(configured_stores))
    if unknown_stores:
        parser.error(f"WB token is not configured for: {', '.join(unknown_stores)}")
    if not stores:
        print("No configured WB stores.", flush=True)
        return 1

    if bool(args.date_from) != bool(args.date_to):
        parser.error("--date-from and --date-to must be used together")
    period = None
    if args.date_from and args.date_to:
        try:
            start = date.fromisoformat(args.date_from)
            inclusive_end = date.fromisoformat(args.date_to)
        except ValueError as exc:
            parser.error(f"invalid date: {exc}")
        if inclusive_end < start:
            parser.error("--date-to must not be earlier than --date-from")
        period = (start, inclusive_end + timedelta(days=1))

    workers = min(max(int(args.workers) if args.workers else len(stores), 1), len(stores))
    report: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wb-orders") as executor:
        futures = {
            executor.submit(_sync_store, store_slug, days, period): store_slug for store_slug in stores
        }
        for future in as_completed(futures):
            store_slug = futures[future]
            try:
                completed_store, result = future.result()
            except Exception as exc:
                logging.exception("WB orders backfill failed for %s", store_slug)
                report[store_slug] = {
                    "ok": False,
                    "rows": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            else:
                report[completed_store] = result

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return 0 if all(result.get("ok") for result in report.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
