from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, sales

TARGET_DAYS = dict(sales.INITIAL_LOOKBACK_DAYS)


def _is_complete(store_slug: str, marketplace: str, days: int) -> bool:
    states = db.get_sales_sync_states(marketplace, store_slug)
    return any(bool(state.get("ok")) and int(state.get("lookback_days") or 0) >= days for state in states)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--marketplace",
        action="append",
        choices=sales.MARKETPLACES,
        help="Limit the backfill to one or more marketplaces.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reload accounts that already have a successful full backfill.",
    )
    args = parser.parse_args()

    marketplaces = args.marketplace or list(sales.MARKETPLACES)
    report: dict[str, dict[str, dict]] = {}

    for marketplace in marketplaces:
        days = TARGET_DAYS[marketplace]
        platform_report: dict[str, dict] = {}
        stores = sales._configured_stores(marketplace)
        print(f"\n[{marketplace}] target={days} days, stores={len(stores)}", flush=True)

        for store_slug in stores:
            if not args.force and _is_complete(store_slug, marketplace, days):
                result = {"ok": True, "skipped": True, "reason": "already complete"}
                platform_report[store_slug] = result
                print(f"  SKIP  {store_slug}: already complete", flush=True)
                continue

            started = time.monotonic()
            print(f"  START {store_slug}", flush=True)
            result = sales.sync_store(store_slug, marketplace, days)
            result["elapsed_seconds"] = round(time.monotonic() - started, 1)
            platform_report[store_slug] = result
            status = "OK" if result.get("ok") else "ERROR"
            print(
                f"  {status:<5} {store_slug}: "
                f"rows={result.get('rows', 0)}, elapsed={result['elapsed_seconds']}s",
                flush=True,
            )
            if result.get("error"):
                print(f"        {result['error']}", flush=True)

        report[marketplace] = platform_report

    print("\nREPORT", flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
