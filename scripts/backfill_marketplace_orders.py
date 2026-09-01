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

SUPPORTED_MARKETPLACES = ("OZON", "YANDEX MARKET")


def _sync_target(store_slug: str, marketplace: str, start: date, end: date) -> tuple[str, str, dict]:
    print(
        f"[{marketplace}/{store_slug}] start, period={start}..{end - timedelta(days=1)}",
        flush=True,
    )
    result = sales.sync_store_period(store_slug, marketplace, start, end)
    state = "ok" if result.get("ok") else "error"
    print(
        f"[{marketplace}/{store_slug}] {state}, rows={result.get('rows', 0)}",
        flush=True,
    )
    return marketplace, store_slug, result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill all Ozon and Yandex Market order statuses for an exact period."
    )
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-to", required=True, help="Inclusive end date")
    parser.add_argument(
        "--marketplace",
        action="append",
        choices=SUPPORTED_MARKETPLACES,
        dest="marketplaces",
    )
    parser.add_argument("--store", action="append", dest="stores")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    try:
        start = date.fromisoformat(args.date_from)
        inclusive_end = date.fromisoformat(args.date_to)
    except ValueError as error:
        parser.error(f"invalid date: {error}")
    if inclusive_end < start:
        parser.error("--date-to must not be earlier than --date-from")
    end = inclusive_end + timedelta(days=1)

    marketplaces = tuple(dict.fromkeys(args.marketplaces or SUPPORTED_MARKETPLACES))
    tasks: list[tuple[str, str]] = []
    for marketplace in marketplaces:
        configured_stores = sales._configured_stores(marketplace)
        selected_stores = args.stores or configured_stores
        missing_credentials = sorted(set(selected_stores) - set(configured_stores))
        if missing_credentials:
            parser.error(
                f"{marketplace} credentials are not configured for: {', '.join(missing_credentials)}"
            )
        tasks.extend((marketplace, store_slug) for store_slug in selected_stores)

    if not tasks:
        print("No configured stores.", flush=True)
        return 1

    workers = min(max(int(args.workers), 1), len(tasks))
    report: dict[str, dict[str, dict]] = {marketplace: {} for marketplace in marketplaces}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="orders-backfill") as executor:
        futures = {
            executor.submit(_sync_target, store_slug, marketplace, start, end): (
                marketplace,
                store_slug,
            )
            for marketplace, store_slug in tasks
        }
        for future in as_completed(futures):
            marketplace, store_slug = futures[future]
            try:
                completed_marketplace, completed_store, result = future.result()
            except Exception as error:
                logging.exception("Orders backfill failed for %s/%s", marketplace, store_slug)
                report[marketplace][store_slug] = {
                    "ok": False,
                    "rows": 0,
                    "error": f"{type(error).__name__}: {error}",
                }
            else:
                report[completed_marketplace][completed_store] = result

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return 0 if all(item.get("ok") for group in report.values() for item in group.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
