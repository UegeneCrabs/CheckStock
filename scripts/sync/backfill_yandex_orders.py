from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.constants import JOBS, YANDEX_ORDERS
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.jobs import locks
from app.repositories import unit_economics_yandex as repository
from app.yandex import tokens, unit_economics_sync
from app.yandex.accounts import resolve_business_id


@dataclass(frozen=True, slots=True, kw_only=True)
class BackfillResult:
    store: str
    date_from: date
    date_to: date
    windows: int
    rows: int
    error_type: str | None = None


def backfill_store(store: str, start: date, end: date) -> BackfillResult:
    windows = rows = 0
    try:
        api_key = tokens.get_api_key(store)
        business_id = resolve_business_id(store, api_key)
        cursor = end + timedelta(days=1)
        while cursor > start:
            first = max(start, cursor - timedelta(days=YANDEX_ORDERS.history_window_days))
            last = cursor - timedelta(days=1)
            data = unit_economics_sync.load_orders(store, api_key, business_id, first, last)
            repository.save_daily(
                store, "orders", data, first.isoformat(), last.isoformat(), datetime.now(UTC).isoformat()
            )
            windows += 1
            rows += len(data)
            print(
                json.dumps(
                    {"store": store, "date_from": str(first), "date_to": str(last), "rows": len(data)},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            cursor = first
    except Exception as error:
        return BackfillResult(
            store=store,
            date_from=start,
            date_to=end,
            windows=windows,
            rows=rows,
            error_type=type(error).__name__,
        )
    return BackfillResult(store=store, date_from=start, date_to=end, windows=windows, rows=rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Восстановить дневные заказы ЯМ FBY+FBS за закрытый период")
    parser.add_argument("--date-from", required=True, type=date.fromisoformat)
    parser.add_argument("--date-to", required=True, type=date.fromisoformat)
    parser.add_argument("--store", action="append", choices=tuple(STORES))
    args = parser.parse_args()
    if args.date_to < args.date_from or args.date_to >= datetime.now(MOSCOW_TIMEZONE).date():
        parser.error("Нужен завершённый период с началом не позже окончания")
    stores = tuple(dict.fromkeys(args.store or tokens.stores_with_credentials()))
    if not stores or any(not tokens.has_credentials(store) for store in stores):
        parser.error("Для выбранных кабинетов не настроены ключи Яндекс Маркета")
    with locks.hold(JOBS.yandex_orders.name), ThreadPoolExecutor(max_workers=len(stores)) as executor:
        results = list(
            executor.map(lambda store: backfill_store(store, args.date_from, args.date_to), stores)
        )
    print(json.dumps([asdict(result) for result in results], ensure_ascii=False, default=str), flush=True)
    return int(any(result.error_type is not None for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
