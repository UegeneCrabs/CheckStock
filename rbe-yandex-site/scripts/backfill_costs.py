"""Fill missing daily cost snapshots with the current public Google Sheet.

This is intentionally additive: manual, Excel and already imported Google
values are preserved. It is useful when cost history did not exist before a
known date and the current price must be used as the opening balance.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main  # noqa: E402


def days(start: date, finish: date):
    current = start
    while current <= finish:
        yield current
        current += timedelta(days=1)


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="Заполнить отсутствующую историю себестоимости")
    parser.add_argument("--from", dest="date_from", type=date.fromisoformat, default=date(2026, 1, 1))
    parser.add_argument("--to", dest="date_to", type=date.fromisoformat, default=datetime.now(main.MOSCOW).date())
    arguments = parser.parse_args()
    if arguments.date_to < arguments.date_from:
        parser.error("Дата окончания не может быть раньше даты начала")

    main.init_database()
    rows, conflicts, articles = main._read_google_costs()
    skipped_rows, scoped_rows, conflicts = main.split_costs_by_store(rows, conflicts)
    now = datetime.now(UTC).isoformat()
    inserted = store_inserted = 0
    with main.connection() as conn:
        run_id = int(conn.execute(
            "INSERT INTO cost_import_runs(started_at,cost_date,source,status) VALUES(?,?,?,?)",
            (now, arguments.date_to.isoformat(), "google", "running"),
        ).lastrowid)
        for article in articles:
            main._upsert_product(conn, article, now)
        for current in days(arguments.date_from, arguments.date_to):
            store_params = [
                (current.isoformat(), row["store_slug"], row["article"], row["cost"], "google", row["sheet_name"], now)
                for row in scoped_rows
            ]
            before = conn.total_changes
            conn.executemany(
                """INSERT OR IGNORE INTO store_product_costs(cost_date,store_slug,article,cost,source,sheet_name,imported_at)
                   VALUES(?,?,?,?,?,?,?)""",
                store_params,
            )
            store_inserted += conn.total_changes - before
            conn.executemany(
                'INSERT OR IGNORE INTO store_cost_conflicts VALUES(?,?,?,?,?)',
                [(current.isoformat(), row['store_slug'], row['article'],
                  json.dumps(row['values'], ensure_ascii=False), now) for row in conflicts],
            )
        detail = json.dumps({
            "date_from": arguments.date_from.isoformat(), "date_to": arguments.date_to.isoformat(),
            "known_articles": len(articles), "inserted_daily_costs": inserted,
            "inserted_store_daily_costs": store_inserted,
            "skipped_without_store": len(skipped_rows),
            "conflicts": conflicts[:100],
        }, ensure_ascii=False)
        conn.execute(
            "UPDATE cost_import_runs SET finished_at=?,status=?,detail=? WHERE id=?",
            (datetime.now(UTC).isoformat(), "partial" if conflicts else "completed", detail, run_id),
        )
    print(json.dumps({"ok": True, "inserted": inserted, "store_inserted": store_inserted,
                      "articles": len(articles), "conflicts": len(conflicts)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
