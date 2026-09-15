"""One-off refresh of the Google arrivals register, using the application's DB."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.infrastructure.database import database_for_path
from app.infrastructure.orm import SupplyArrivalsSnapshotRecord
from app.jobs.locks import SyncJobBusyError
from app.jobs.tracking import run_tracked
from app.repositories import core
from app.stock import supply_arrivals


def main() -> int:
    # Only this feature's additive table is needed; do not run unrelated migrations.
    database = database_for_path(core.DB_PATH)
    SupplyArrivalsSnapshotRecord.__table__.create(database.engine, checkfirst=True)
    try:
        result = run_tracked(supply_arrivals.JOB_NAME, "manual", supply_arrivals.sync)
    except SyncJobBusyError:
        result = {"status": "running", "message": "Выгрузка уже выполняется"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("status") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
