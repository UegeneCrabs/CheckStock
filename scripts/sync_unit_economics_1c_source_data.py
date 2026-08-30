"""Вручную обновить данные юнит-экономики 1С из всех вкладок Google Sheets *WB."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app import unit_economics_1c_source_data as source_data  # noqa: E402


def main() -> None:
    db.init_db()
    report = source_data.sync_all()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
