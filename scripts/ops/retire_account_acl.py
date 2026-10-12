"""Back up and migrate an offline SQLite database; without --apply only inspect."""

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.infrastructure.database import Database
from app.repositories.identity_migration import RETIRED_TABLES, RETIRED_USER_COLUMNS, retire_access_control

USER_COLUMNS = ("id", "full_name", "google_email", "login", "password_hash", "is_active", "created_at")


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def snapshot(path):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        tables = {
            name: sql
            for name, sql in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        retained = set(tables) - set(RETIRED_TABLES)
        counts = {
            name: connection.execute(f"SELECT COUNT(*) FROM {quoted(name)}").fetchone()[0]
            for name in retained
        }
        digests = {}
        for name in retained & {"users", "sessions", "activity_log"}:
            columns = ",".join(USER_COLUMNS) if name == "users" else "*"
            order = "token" if name == "sessions" else "id"
            digest = hashlib.sha256()
            for row in connection.execute(f"SELECT {columns} FROM {quoted(name)} ORDER BY {order}"):
                digest.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode())
                digest.update(b"\n")
            digests[name] = digest.hexdigest()
        return {"tables": tables, "counts": counts, "digests": digests}


def backup_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Never replace an existing backup or the source.
    with destination.open("xb"):
        pass
    with (
        sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as reader,
        sqlite3.connect(destination) as writer,
    ):
        reader.backup(writer, pages=2048)
        if writer.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise RuntimeError("Backup integrity check failed")


def verify_preserved(before, after):
    if before["counts"] != after["counts"] or before["digests"] != after["digests"]:
        raise RuntimeError(
            "Retained data changed during migration; keep the service stopped and inspect backup"
        )
    expected = set(before["tables"]) - set(RETIRED_TABLES)
    if set(after["tables"]) != expected:
        raise RuntimeError("Unexpected schema after migration")
    for table in expected - {"users"}:
        if before["tables"][table] != after["tables"][table]:
            raise RuntimeError(f"Unexpected schema change: {table}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--backup", type=Path, help="New backup filename (default: timestamp next to database)"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Service must be stopped; back up, migrate, verify"
    )
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("Database does not exist")
    before = snapshot(args.database)
    removed = sorted(set(before["tables"]) & set(RETIRED_TABLES))
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    print(
        json.dumps(
            {
                "tables_to_remove": removed,
                "user_columns_to_remove": sorted(columns & set(RETIRED_USER_COLUMNS)),
                "tables_before": len(before["tables"]),
                "retained_tables": len(before["counts"]),
            },
            indent=2,
        )
    )
    if not args.apply:
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = args.backup or args.database.with_name(f"{args.database.stem}.before-acl-retirement-{stamp}.db")
    backup_database(args.database, backup)
    print(f"Backup verified: {backup}", flush=True)
    if snapshot(backup) != before:
        raise RuntimeError("Database changed during backup. Stop all writers before retrying")
    database = Database(path=args.database)
    try:
        retire_access_control(database)
    finally:
        database.dispose()
    verify_preserved(before, snapshot(args.database))
    print("Migration verified: retained tables, user data, sessions and activity are unchanged")


if __name__ == "__main__":
    main()
