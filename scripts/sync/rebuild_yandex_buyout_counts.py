"""Backfill Yandex daily P&L buyout quantities from official API events."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.repositories import yandex_financial


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True)
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-to", required=True)
    args = parser.parse_args()
    updated = yandex_financial.rebuild_daily_buyout_counts(args.store, args.date_from, args.date_to)
    print(json.dumps({"store": args.store, "days_updated": updated}))


if __name__ == "__main__":
    main()
