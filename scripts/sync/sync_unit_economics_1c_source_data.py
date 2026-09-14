"""Загрузить ЗЦ всех площадок по артикулам и данные WB из вкладок Google Sheets *WB."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.economics.sources import purchase_prices as source_data  # noqa: E402


def main() -> None:
    db.init_db()
    report = source_data.sync_all()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
