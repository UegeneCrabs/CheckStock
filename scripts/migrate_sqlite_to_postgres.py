from __future__ import annotations

import argparse
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Connection, Engine, Integer, create_engine, func, insert, select, text
from sqlalchemy.engine import make_url

from app.infrastructure.orm import OrmBase


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy a CheckStock SQLite database into an empty PostgreSQL database."
    )
    parser.add_argument("sqlite_path", type=Path)
    parser.add_argument(
        "--database-url",
        default=os.getenv("CHECKSTOCK_DATABASE_URL", ""),
        help="PostgreSQL SQLAlchemy URL; defaults to CHECKSTOCK_DATABASE_URL.",
    )
    parser.add_argument("--batch-size", type=int, default=2_000)
    return parser.parse_args()


def _source_connection(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {resolved}")
    connection = sqlite3.connect(
        f"file:{resolved.as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        connection.close()
        raise RuntimeError(f"SQLite integrity check failed: {integrity}")
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        connection.close()
        raise RuntimeError(f"SQLite foreign key check failed: {len(foreign_key_errors)} errors")
    return connection


def _source_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _batches(cursor: sqlite3.Cursor, size: int) -> Iterator[list[dict[str, object]]]:
    while rows := cursor.fetchmany(size):
        yield [dict(row) for row in rows]


def _reset_sequences(connection: Connection) -> None:
    for table in OrmBase.metadata.sorted_tables:
        if len(table.primary_key.columns) != 1:
            continue
        column = next(iter(table.primary_key.columns))
        if not isinstance(column.type, Integer) or not column.autoincrement:
            continue
        sequence = connection.scalar(
            text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
            {"table_name": table.name, "column_name": column.name},
        )
        if not sequence:
            continue
        maximum = connection.scalar(select(func.max(column)))
        connection.execute(
            text("SELECT setval(CAST(:sequence AS regclass), :value, :is_called)"),
            {
                "sequence": sequence,
                "value": int(maximum or 1),
                "is_called": maximum is not None,
            },
        )


def migrate(sqlite_path: Path, engine: Engine, *, batch_size: int = 2_000) -> dict[str, int]:
    if engine.dialect.name != "postgresql":
        raise ValueError("migration target must be PostgreSQL")
    if batch_size < 1:
        raise ValueError("batch size must be positive")

    source = _source_connection(sqlite_path)
    try:
        source_tables = _source_tables(source)
        OrmBase.metadata.create_all(engine)
        copied: dict[str, int] = {}
        with engine.begin() as target:
            for table in OrmBase.metadata.sorted_tables:
                existing = int(target.scalar(select(func.count()).select_from(table)) or 0)
                if existing:
                    raise RuntimeError(
                        f"target table {table.name} is not empty ({existing} rows); migration aborted"
                    )

            for table in OrmBase.metadata.sorted_tables:
                if table.name not in source_tables:
                    copied[table.name] = 0
                    continue
                source_columns = {
                    str(row[1]) for row in source.execute(f'PRAGMA table_info("{table.name}")')
                }
                columns = [column.name for column in table.columns if column.name in source_columns]
                quoted_columns = ", ".join(f'"{column}"' for column in columns)
                cursor = source.execute(f'SELECT {quoted_columns} FROM "{table.name}"')
                total = 0
                for batch in _batches(cursor, batch_size):
                    target.execute(insert(table), batch)
                    total += len(batch)
                copied[table.name] = total

            _reset_sequences(target)

            for table in OrmBase.metadata.sorted_tables:
                actual = int(target.scalar(select(func.count()).select_from(table)) or 0)
                if actual != copied[table.name]:
                    raise RuntimeError(
                        f"row count mismatch for {table.name}: source={copied[table.name]} target={actual}"
                    )
        return copied
    finally:
        source.close()


def main() -> None:
    arguments = _arguments()
    if not arguments.database_url:
        raise SystemExit("CHECKSTOCK_DATABASE_URL or --database-url is required")
    url = make_url(arguments.database_url)
    if url.get_backend_name() != "postgresql":
        raise SystemExit("migration target must be PostgreSQL")
    print(f"target: {url.render_as_string(hide_password=True)}")
    engine = create_engine(url, pool_pre_ping=True)
    try:
        copied = migrate(arguments.sqlite_path, engine, batch_size=arguments.batch_size)
    finally:
        engine.dispose()
    print(f"migration ok: tables={len(copied)} rows={sum(copied.values())}")
    for table, count in copied.items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()
