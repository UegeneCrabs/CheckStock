"""Rebuild daily Yandex P&L from already imported official report rows."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.repositories.core import get_connection
from app.yandex import financial_reports, tokens


def _sheets(store_slug: str, date_from: str, date_to: str) -> tuple[int, dict[str, list[dict]], dict[str, list[dict]]]:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT business_id,report_kind,sheet,payload_json
            FROM yandex_financial_report_rows
            WHERE store_slug=? AND period_from=? AND period_to=?
              AND report_kind IN ('united-orders', 'united-services')
            """,
            (store_slug, date_from, date_to),
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        raise ValueError("В базе нет полного финансового отчёта за выбранный период")
    business_ids = {int(row["business_id"]) for row in rows}
    if len(business_ids) != 1:
        raise ValueError("Для пересчёта нужен один кабинет Яндекс Маркета")
    order_sheets: defaultdict[str, list[dict]] = defaultdict(list)
    service_sheets: defaultdict[str, list[dict]] = defaultdict(list)
    for row in rows:
        target = order_sheets if row["report_kind"] == "united-orders" else service_sheets
        target[str(row["sheet"])].append(json.loads(row["payload_json"]))
    return business_ids.pop(), dict(order_sheets), dict(service_sheets)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True)
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-to", required=True)
    args = parser.parse_args()
    business_id, order_sheets, service_sheets = _sheets(args.store, args.date_from, args.date_to)
    accounts = tokens.get_accounts(args.store)
    account = next((item for item in accounts if int(item["business_id"]) == business_id), {})
    financial_reports._store_daily_pnl(
        args.store,
        business_id,
        date.fromisoformat(args.date_from),
        date.fromisoformat(args.date_to),
        service_sheets,
        order_sheets,
        datetime.now(UTC).isoformat(timespec="seconds"),
        campaign_count=len(account.get("campaign_ids") or []),
    )
    print(json.dumps({"store": args.store, "days_rebuilt": (date.fromisoformat(args.date_to) - date.fromisoformat(args.date_from)).days + 1}))


if __name__ == "__main__":
    main()
