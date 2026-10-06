"""One Google destination per marketplace, shared by every project."""

from __future__ import annotations

from dataclasses import dataclass

from app.repositories.core import WRITE_LOCK, get_connection
from app.repositories.stock_sheet_export import MARKETPLACES


@dataclass(frozen=True, slots=True)
class MarketplaceExportTarget:
    marketplace: str
    stock_sheet_name: str
    orders_sheet_name: str
    spreadsheet_url: str = ""
    orders_quantity_column: str = "C"


@dataclass(frozen=True, slots=True)
class ProjectSheetExportSettings:
    enabled: bool
    schedule_kind: str
    weekday: int
    run_time: str
    updated_at: str
    last_attempt_at: str | None
    last_success_at: str | None
    last_error: str | None
    targets: tuple[MarketplaceExportTarget, ...]

    def target(self, marketplace: str) -> MarketplaceExportTarget:
        for target in self.targets:
            if target.marketplace == marketplace:
                return target
        raise KeyError(f"Не настроена выгрузка {marketplace}")


def get_settings() -> ProjectSheetExportSettings | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM project_sheet_export_settings WHERE id = 1").fetchone()
        if row is None:
            return None
        targets = {
            target["marketplace"]: MarketplaceExportTarget(
                marketplace=str(target["marketplace"]),
                stock_sheet_name=str(target["stock_sheet_name"]),
                orders_sheet_name=str(target["orders_sheet_name"]),
                spreadsheet_url=str(target["spreadsheet_url"]),
                orders_quantity_column=str(target["orders_quantity_column"]),
            )
            for target in conn.execute("SELECT * FROM project_sheet_export_targets").fetchall()
        }
    return ProjectSheetExportSettings(
        enabled=bool(row["enabled"]),
        schedule_kind=str(row["schedule_kind"]),
        weekday=int(row["weekday"]),
        run_time=str(row["run_time"]),
        updated_at=str(row["updated_at"]),
        last_attempt_at=row["last_attempt_at"],
        last_success_at=row["last_success_at"],
        last_error=row["last_error"],
        targets=tuple(targets[marketplace] for marketplace in MARKETPLACES if marketplace in targets),
    )


def save_settings(settings: ProjectSheetExportSettings, *, only_if_missing: bool = False) -> None:
    conflict_action = (
        "DO NOTHING"
        if only_if_missing
        else """DO UPDATE SET
                enabled = excluded.enabled, schedule_kind = excluded.schedule_kind,
                weekday = excluded.weekday, run_time = excluded.run_time,
                updated_at = excluded.updated_at"""
    )
    with WRITE_LOCK, get_connection() as conn:
        conn.execute("BEGIN")
        inserted = conn.execute(
            f"""
            INSERT INTO project_sheet_export_settings
                (id, enabled, schedule_kind, weekday, run_time, spreadsheet_url, updated_at,
                 last_attempt_at, last_success_at, last_error)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) {conflict_action}
            """,
            (
                int(settings.enabled),
                settings.schedule_kind,
                settings.weekday,
                settings.run_time,
                "",  # Retained database column from the initial single-file configuration.
                settings.updated_at,
                settings.last_attempt_at,
                settings.last_success_at,
                settings.last_error,
            ),
        )
        if only_if_missing and inserted.rowcount == 0:
            conn.commit()
            return
        conn.execute("DELETE FROM project_sheet_export_targets")
        conn.executemany(
            """INSERT INTO project_sheet_export_targets
                   (marketplace, stock_sheet_name, orders_sheet_name, spreadsheet_url, orders_quantity_column)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (
                    target.marketplace,
                    target.stock_sheet_name,
                    target.orders_sheet_name,
                    target.spreadsheet_url,
                    target.orders_quantity_column,
                )
                for target in settings.targets
            ],
        )
        conn.commit()


def record_attempt(attempted_at: str) -> None:
    with WRITE_LOCK, get_connection() as conn:
        conn.execute(
            "UPDATE project_sheet_export_settings SET last_attempt_at = ? WHERE id = 1", (attempted_at,)
        )
        conn.commit()


def record_success(exported_at: str) -> None:
    """A scoped success must not postpone the next full scheduled export."""
    with WRITE_LOCK, get_connection() as conn:
        conn.execute(
            """UPDATE project_sheet_export_settings
               SET last_success_at = ?,
                   last_error = CASE WHEN last_attempt_at IS NULL OR last_attempt_at <= ?
                                     THEN NULL ELSE last_error END
               WHERE id = 1""",
            (exported_at, exported_at),
        )
        conn.commit()


def record_result(attempted_at: str, *, error: str | None) -> None:
    with WRITE_LOCK, get_connection() as conn:
        if error is None:
            conn.execute(
                """UPDATE project_sheet_export_settings
                   SET last_attempt_at = ?, last_success_at = ?, last_error = NULL WHERE id = 1""",
                (attempted_at, attempted_at),
            )
        else:
            conn.execute(
                """UPDATE project_sheet_export_settings
                   SET last_attempt_at = ?, last_error = ? WHERE id = 1""",
                (attempted_at, error),
            )
        conn.commit()
