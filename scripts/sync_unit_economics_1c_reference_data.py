"""Загрузить ABC-коды, комиссии СУ и категории WB для юнит-экономики 1С."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app import unit_economics_1c_reference_data as reference_sync
from app.stores import STORES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        choices=("all", "classifications", "commissions", "categories"),
        default="all",
        help="Какой справочник обновить; по умолчанию — все.",
    )
    parser.add_argument(
        "--store",
        action="append",
        choices=tuple(STORES),
        help="Кабинет для категорий WB. Можно повторить; по умолчанию — все.",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    db.init_db()
    stores = tuple(dict.fromkeys(args.store or STORES))
    report: dict[str, object] = {}
    if args.scope in {"all", "classifications"}:
        report["classifications"] = reference_sync.sync_product_classifications()
    if args.scope in {"all", "commissions"}:
        report["commissions"] = reference_sync.sync_wb_commissions()
    if args.scope in {"all", "categories"}:
        report["categories"] = reference_sync.sync_product_categories_for_stores(stores)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return (
        0
        if all(
            result.get("ok", True)
            for group in report.values()
            for result in (group.values() if isinstance(group, dict) and "ok" not in group else [group])
            if isinstance(result, dict)
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
