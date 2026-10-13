"""One-time migration of existing Yandex RBE facts into the isolated database."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SITE))
from main import connection, init_database  # noqa: E402
from recalculate_cost_of_goods import recalculate  # noqa: E402
from report_quality import build_quality, save_quality  # noqa: E402


def main(
    source_path: str,
    stores: tuple[str, ...] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> None:
    """Copy only the requested official facts into the isolated RBE database.

    With no filters this remains a full migration. The importer uses the
    filters during a targeted repair so facts that are already complete are
    not rewritten.
    """
    source = sqlite3.connect(f"file:{Path(source_path).resolve().as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute('PRAGMA query_only=ON')
    source.execute('BEGIN')
    init_database()
    filters: list[str] = []
    params: list[object] = []
    if stores:
        filters.append(f"store_slug IN ({','.join('?' for _ in stores)})")
        params.extend(stores)
    if date_from:
        filters.append("report_date>=?")
        params.append(date_from.isoformat())
    if date_to:
        filters.append("report_date<=?")
        params.append(date_to.isoformat())
    where = f" WHERE {' AND '.join(filters)}" if filters else ""
    daily = source.execute(
        "SELECT store_slug,business_id,report_date,values_json,calculated_at FROM yandex_financial_daily_pnl" + where,
        params,
    ).fetchall()

    order_filters: list[str] = ["marketplace='YANDEX MARKET'", "ordered_at IS NOT NULL"]
    order_params: list[object] = []
    if stores:
        order_filters.append(f"store_slug IN ({','.join('?' for _ in stores)})")
        order_params.extend(stores)
    if date_from:
        order_filters.append("substr(ordered_at,1,10)>=?")
        order_params.append(date_from.isoformat())
    if date_to:
        order_filters.append("substr(ordered_at,1,10)<=?")
        order_params.append(date_to.isoformat())
    orders = source.execute(
        """
        SELECT store_slug,substr(ordered_at,1,10) AS report_date,
               SUM(CASE WHEN order_amount-cancelled_amount>0 THEN order_amount-cancelled_amount ELSE 0 END) AS orders_amount,
               SUM(cancelled_amount) AS cancellations_amount
        FROM sales_order_lines
        WHERE """ + " AND ".join(order_filters) + """
        GROUP BY store_slug,substr(ordered_at,1,10)
        """,
        order_params,
    ).fetchall()
    quality = build_quality(source, daily)
    source.close()
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
        save_quality(target, quality)
    costs = recalculate(source_path, stores=stores, date_from=date_from, date_to=date_to)
    print(json.dumps({"financial_days": len(daily), "order_days": len(orders), "costs": costs}, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/import_from_checkstock.py C:\\path\\to\\checkstock.db")
    main(sys.argv[1])
