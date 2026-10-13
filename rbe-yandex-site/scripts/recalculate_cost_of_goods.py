"""Rebuild RBE COGS from the official Yandex redemption basket and local costs.

The Market financial archive defines which exact order lines are redeemed or
returned.  This module deliberately reuses that canonical basket rather than
using a separately aggregated sales table, so turnover, buyout quantity and
cost of goods describe the same products.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
ROOT = SITE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SITE))

from app.yandex import financial_reports  # noqa: E402
from main import connection  # noqa: E402

ORDER_KIND = "united-orders"
SERVICE_KIND = "united-services"


def _stable_archive_key(payload: dict) -> str:
    """Identify an official operation independently of report snapshot metadata.

    Market repeats the same operation in overlapping report periods.  Those
    copies differ only by ``actDate`` (the report-generation date), while the
    order, SKU, service timestamp and money fields are identical.  ``actDate``
    must not turn one financial event into several sold units.
    """

    operation = dict(payload)
    operation.pop("actDate", None)
    return json.dumps(operation, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_reports(source: sqlite3.Connection) -> dict[tuple[str, int], dict[str, dict[str, list[dict]]]]:
    rows = source.execute(
        """SELECT store_slug,business_id,report_kind,sheet,payload_json,period_to,imported_at
           FROM yandex_financial_report_rows
           WHERE report_kind IN (?,?)""",
        (ORDER_KIND, SERVICE_KIND),
    ).fetchall()
    grouped: dict[tuple[str, int], dict[str, dict[str, dict[str, tuple[str, str, dict]]]]] = defaultdict(
        lambda: {ORDER_KIND: defaultdict(dict), SERVICE_KIND: defaultdict(dict)}
    )
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        snapshots = grouped[(str(row["store_slug"]), int(row["business_id"]))][str(row["report_kind"])][str(row["sheet"])]
        identity = _stable_archive_key(payload)
        version = (str(row["period_to"]), str(row["imported_at"]))
        previous = snapshots.get(identity)
        # Overlapping reports are revised periodically; use the latest report
        # snapshot for an operation rather than counting historical copies.
        if previous is None or version >= previous[:2]:
            snapshots[identity] = (*version, payload)

    result: dict[tuple[str, int], dict[str, dict[str, list[dict]]]] = {}
    for account, report_kinds in grouped.items():
        result[account] = {
            kind: {
                sheet: [version[2] for version in snapshots.values()]
                for sheet, snapshots in sheets.items()
            }
            for kind, sheets in report_kinds.items()
        }
    return result


def _prices_for_day(target: sqlite3.Connection, store: str, day: str) -> dict[str, float]:
    prices = {
        str(row["article"]): float(row["cost"])
        for row in target.execute(
            "SELECT article,cost FROM store_product_costs WHERE store_slug=? AND cost_date=?", (store, day)
        )
    }
    # A global manual/Excel price is a fallback only where the store tab does
    # not contain this SKU.
    for row in target.execute("SELECT article,cost FROM product_costs WHERE cost_date=?", (day,)):
        prices.setdefault(str(row["article"]), float(row["cost"]))
    return prices


def recalculate(
    source_path: str | Path,
    stores: tuple[str, ...] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, int]:
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    reports = _source_reports(source)
    recalculated = incomplete = without_source = 0
    with connection() as target:
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
        daily_rows = target.execute(
            "SELECT store_slug,business_id,report_date,values_json FROM daily_financial" + where,
            params,
        ).fetchall()
        for row in daily_rows:
            key = (str(row["store_slug"]), int(row["business_id"]))
            report_set = reports.get(key)
            if not report_set:
                without_source += 1
                continue
            day = str(row["report_date"])
            daily_orders, _ = financial_reports._daily_order_sheets(report_set[ORDER_KIND], day)
            daily_services = financial_reports._daily_service_sheets(report_set[SERVICE_KIND], day)
            redeemed, returned = financial_reports._buyout_basket(daily_services, daily_orders)
            sold_cost = financial_reports._cost_for_items(redeemed, _prices_for_day(target, key[0], day))
            returned_cost = financial_reports._cost_for_items(returned, _prices_for_day(target, key[0], day))
            values = json.loads(row["values_json"])
            if sold_cost is None or returned_cost is None:
                values.update({"sold_cost_of_goods": None, "returned_cost_of_goods": None, "cost_of_goods": None})
                incomplete += 1
            else:
                net_cost = round(sold_cost - returned_cost, 2)
                values.update({
                    "sold_cost_of_goods": net_cost,
                    "returned_cost_of_goods": returned_cost,
                    "cost_of_goods": net_cost,
                })
                recalculated += 1
            target.execute(
                # Cost-only recalculation does not change the financial-source
                # version. Otherwise it invalidates daily_source_quality just
                # written by the importer, including confirmed zero order days.
                """UPDATE daily_financial SET values_json=?
                   WHERE store_slug=? AND business_id=? AND report_date=?""",
                (json.dumps(values, ensure_ascii=False), key[0], key[1], day),
            )
    source.close()
    return {"recalculated": recalculated, "incomplete": incomplete, "without_source": without_source}


if __name__ == "__main__":
    database = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "checkstock.db")
    print(json.dumps(recalculate(database), ensure_ascii=False))
