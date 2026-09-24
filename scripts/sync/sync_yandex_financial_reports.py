from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.yandex import financial_reports


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import Yandex financial order reports into the local database."
    )
    parser.add_argument("--store", required=True)
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-to", required=True)
    args = parser.parse_args()
    try:
        result = financial_reports.import_period(
            args.store, date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
        )
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
