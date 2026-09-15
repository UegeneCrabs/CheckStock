"""One atomic snapshot per Google spreadsheet / sheet. Errors retain good data."""

import json

from app.infrastructure.database import database_for_path
from app.infrastructure.orm import SupplyArrivalsSnapshotRecord as Snapshot
from app.repositories import core


def read(source_key: str) -> dict:
    with database_for_path(core.DB_PATH).session_factory() as session:
        record = session.get(Snapshot, source_key)
        if record is None:
            return {"rows": [], "last_attempt": None, "last_success": None, "error": "", "sheet_title": ""}
        return {
            "rows": json.loads(record.payload_json),
            "last_attempt": record.last_attempt,
            "last_success": record.last_success,
            "error": record.error,
            "sheet_title": record.sheet_title,
        }


def save(source_key: str, **values) -> None:
    # All writers hold the shared job lock. The single transaction replaces
    # the payload only after the full sheet has been fetched and validated.
    with database_for_path(core.DB_PATH).session_factory() as session:
        record = session.get(Snapshot, source_key)
        if record is None:
            record = Snapshot(source_key=source_key)
            session.add(record)
        for key, value in values.items():
            setattr(record, key, value)
        session.commit()
