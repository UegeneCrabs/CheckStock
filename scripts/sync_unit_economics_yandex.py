"""Обновить показатели юнит-экономики ЯМ в отдельном хранилище."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.stores import STORES
from app.yandex import product_novelty, unit_economics_sync


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", action="append", choices=tuple(STORES), required=True)
    parser.add_argument(
        "--source", choices=(*unit_economics_sync.SOURCES, product_novelty.SOURCE), required=True
    )
    args = parser.parse_args()
    db.init_db()
    report = {
        slug: product_novelty.sync_store(slug)
        if args.source == product_novelty.SOURCE
        else unit_economics_sync.sync_store(slug, args.source)
        for slug in dict.fromkeys(args.store)
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(item["ok"] for item in report.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
