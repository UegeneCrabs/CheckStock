from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import stock_sheet_export


def _parse_period(parser: argparse.ArgumentParser, date_from: str, date_to: str) -> tuple[date, date]:
    try:
        start = date.fromisoformat(date_from)
        inclusive_end = date.fromisoformat(date_to)
    except ValueError as error:
        parser.error(f"invalid date: {error}")
    if inclusive_end < start:
        parser.error("--date-to must not be earlier than --date-from")
    return start, inclusive_end + timedelta(days=1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export FBS order totals for an exact period to configured Google Sheets."
    )
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-to", required=True, help="Inclusive end date")
    parser.add_argument("--store", action="append", dest="stores")
    args = parser.parse_args()

    start, end = _parse_period(parser, args.date_from, args.date_to)
    requested_stores = tuple(dict.fromkeys(args.stores or stock_sheet_export.STORES))
    unknown_stores = sorted(set(requested_stores) - set(stock_sheet_export.STORES))
    if unknown_stores:
        parser.error(f"unknown stores: {', '.join(unknown_stores)}")

    stock_sheet_export.ensure_defaults()
    all_settings = stock_sheet_export.list_settings()
    settings_by_store = {settings.store_slug: settings for settings in all_settings}
    missing_settings = sorted(set(requested_stores) - set(settings_by_store))
    if missing_settings:
        parser.error(f"settings are missing for: {', '.join(missing_settings)}")

    period_days = (end - start).days
    stock_sheet_export.FBS_ORDER_LOOKBACK_DAYS = period_days
    stock_sheet_export.ORDER_EXPORT_HEADERS = (
        stock_sheet_export.ORDER_EXPORT_HEADERS[0],
        f"ЗАКАЗЫ FBS {start:%d.%m.%Y}–{end - timedelta(days=1):%d.%m.%Y}",
    )
    export_now = datetime.combine(end, time.min, tzinfo=stock_sheet_export.MOSCOW_TIMEZONE)

    report: dict[str, object] = {
        "period": {"date_from": start.isoformat(), "date_to": (end - timedelta(days=1)).isoformat()},
        "destinations": [],
        "skipped": [],
    }
    destinations: list[dict] = report["destinations"]  # type: ignore[assignment]
    skipped: list[dict] = report["skipped"]  # type: ignore[assignment]
    processed: set[tuple[str, str, str]] = set()
    service = None

    for store_slug in requested_stores:
        settings = settings_by_store[store_slug]
        for marketplace in stock_sheet_export.repository.MARKETPLACES:
            sheet_name = stock_sheet_export._target_sheet_name(
                settings, marketplace, "fbs_orders"
            ).strip()
            if not sheet_name:
                skipped.append(
                    {
                        "store_slug": store_slug,
                        "marketplace": marketplace,
                        "reason": "FBS orders sheet is not configured",
                    }
                )
                continue

            try:
                spreadsheet_id = stock_sheet_export._spreadsheet_id(
                    settings.spreadsheet_url_for(marketplace)
                )
                destination_key = (spreadsheet_id, marketplace, sheet_name.casefold())
                if destination_key in processed:
                    continue
                processed.add(destination_key)

                destination_stores = tuple(
                    candidate
                    for candidate in stock_sheet_export._stores_for_destination(
                        settings,
                        marketplace,
                        all_settings,
                        "fbs_orders",
                    )
                    if candidate in requested_stores
                )
                totals = stock_sheet_export._combined_fbs_order_totals(
                    destination_stores,
                    marketplace,
                    now=export_now,
                )
                if service is None:
                    service = stock_sheet_export._google_service()
                destination_report = stock_sheet_export._write_fbs_orders(
                    service,
                    spreadsheet_id,
                    settings,
                    marketplace,
                    totals,
                )
                destinations.append(
                    {
                        "ok": True,
                        "marketplace": marketplace,
                        "sheet": sheet_name,
                        "store_slugs": destination_stores,
                        "rows": destination_report["rows"],
                        "updated_cells": destination_report["updated_cells"],
                    }
                )
            except Exception as error:
                destinations.append(
                    {
                        "ok": False,
                        "marketplace": marketplace,
                        "sheet": sheet_name,
                        "store_slugs": [store_slug],
                        "error": f"{type(error).__name__}: {error}",
                    }
                )

    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 2 if any(not item["ok"] for item in destinations) else 0


if __name__ == "__main__":
    raise SystemExit(main())
