"""One-time migration of existing Yandex RBE facts into the isolated database."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SITE))
from main import connection, init_database  # noqa: E402


def main(source_path: str) -> None:
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    init_database()
    daily = source.execute(
        "SELECT store_slug,business_id,report_date,values_json,calculated_at FROM yandex_financial_daily_pnl"
    ).fetchall()
    orders = source.execute(
        """
        SELECT store_slug,substr(ordered_at,1,10) AS report_date,
               SUM(CASE WHEN order_amount-cancelled_amount>0 THEN order_amount-cancelled_amount ELSE 0 END) AS orders_amount,
               SUM(cancelled_amount) AS cancellations_amount
        FROM sales_order_lines
        WHERE marketplace='YANDEX MARKET' AND ordered_at IS NOT NULL
        GROUP BY store_slug,substr(ordered_at,1,10)
        """
    ).fetchall()
    with connection() as target:
        target.executemany(
            """
            INSERT INTO daily_financial(store_slug,business_id,report_date,values_json,calculated_at)
            VALUES(?,?,?,?,?) ON CONFLICT(store_slug,business_id,report_date) DO UPDATE SET
            values_json=excluded.values_json,calculated_at=excluded.calculated_at
            """, [tuple(row) for row in daily]
        )
        target.executemany(
            """
            INSERT INTO daily_orders(store_slug,report_date,orders_amount,cancellations_amount)
            VALUES(?,?,?,?) ON CONFLICT(store_slug,report_date) DO UPDATE SET
            orders_amount=excluded.orders_amount,cancellations_amount=excluded.cancellations_amount
            """, [tuple(row) for row in orders]
        )
    print(json.dumps({"financial_days": len(daily), "order_days": len(orders)}, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/import_from_checkstock.py C:\\path\\to\\checkstock.db")
    main(sys.argv[1])
