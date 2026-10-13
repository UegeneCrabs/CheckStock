"""Rebuild daily RBE financial metrics from archived official Market reports.

Use this after a formula correction.  It reuses the newest version of every
official operation and never calls the API, so it is safe while Market has an
active-report limit.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
ROOT = SITE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SITE))

from app.yandex import financial_reports, tokens  # noqa: E402
from import_from_checkstock import main as copy_into_rbe  # noqa: E402

KINDS = ("united-orders", "united-services", "united-netting")


def _identity(payload: dict) -> str:
    """Ignore report-generation metadata when comparing archive snapshots."""

    payload = dict(payload)
    payload.pop("actDate", None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _latest_reports(
    source: sqlite3.Connection, store: str, business_id: int, report_from: date, report_to: date
) -> dict[str, dict[str, list[dict]]]:
    reports: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in source.execute(
        """SELECT report_kind,sheet,payload_json,period_to,imported_at
           FROM yandex_financial_report_rows
           WHERE store_slug=? AND business_id=? AND report_kind IN (?,?,?)
             AND period_from=? AND period_to=?""",
        (store, business_id, *KINDS, report_from.isoformat(), report_to.isoformat()),
    ):
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        # A single exact report period is an authoritative export.  Do not
        # collapse rows inside it: distinct service rows can have identical
        # visible fields but still be separate official accruals.
        reports[row["report_kind"]][row["sheet"]].append(payload)
    return reports


def rebuild(
    source_path: Path, date_from: date, date_to: date, stores: tuple[str, ...], report_to: date
) -> int:
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    rebuilt = 0
    try:
        for store in stores:
            for account in tokens.get_accounts(store):
                business_id = int(account["business_id"])
                reports = _latest_reports(source, store, business_id, date_from, report_to)
                services = reports.get("united-services", {})
                orders = reports.get("united-orders", {})
                if not services or not orders or not reports.get("united-netting"):
                    print(json.dumps({"store": store, "business_id": business_id, "rebuilt": False, "reason": "no archived reports"}, ensure_ascii=False))
                    continue
                financial_reports._store_daily_pnl(
                    store,
                    business_id,
                    date_from,
                    date_to,
                    services,
                    orders,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    campaign_count=len(account.get("campaign_ids") or []),
                )
                rebuilt += 1
                print(json.dumps({"store": store, "business_id": business_id, "rebuilt": True}, ensure_ascii=False))
    finally:
        source.close()
    if rebuilt:
        copy_into_rbe(str(source_path), stores=stores, date_from=date_from, date_to=date_to)
    return rebuilt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date-from", required=True, type=date.fromisoformat)
    parser.add_argument("--date-to", required=True, type=date.fromisoformat)
    parser.add_argument(
        "--report-to",
        type=date.fromisoformat,
        help="Дата конца архивного отчёта, из которого пересчитывается выбранный период.",
    )
    parser.add_argument("--stores", nargs="+", default=("rimili", "tris", "rockkiddo", "trusthome", "sokoloff", "gogol"))
    args = parser.parse_args()
    if args.date_to < args.date_from:
        parser.error("Дата окончания раньше даты начала")
    report_to = args.report_to or args.date_to
    if report_to < args.date_to:
        parser.error("Архивный отчёт не покрывает выбранный период")
    count = rebuild(ROOT / "data" / "checkstock.db", args.date_from, args.date_to, tuple(args.stores), report_to)
    print(json.dumps({"rebuilt_accounts": count}, ensure_ascii=False))
