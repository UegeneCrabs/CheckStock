from __future__ import annotations

from dataclasses import dataclass

from app.repositories.core import WRITE_LOCK, get_connection

MARKETPLACES = ("WB", "OZON", "YANDEX MARKET")
STOCK_METRICS = ("ff_stock", "fbs_stock", "fbo_stock")
METRICS = (*STOCK_METRICS, "fbs_orders")
METRICS_BY_MARKETPLACE = {
    "WB": (*STOCK_METRICS, "fbs_orders"),
    "OZON": (*STOCK_METRICS, "fbs_orders"),
    "YANDEX MARKET": STOCK_METRICS,
}


def allowed_metrics(marketplace: str) -> tuple[str, ...]:
    try:
        return METRICS_BY_MARKETPLACE[marketplace]
    except KeyError as error:
        raise ValueError(f"Неизвестный маркетплейс: {marketplace}") from error


@dataclass(frozen=True, slots=True)
class ExportTarget:
    marketplace: str
    metric: str
    sheet_name: str
    key_column_name: str
    value_column_name: str
    id: int | None = None


@dataclass(frozen=True, slots=True)
class MarketplaceSpreadsheet:
    marketplace: str
    spreadsheet_url: str


@dataclass(frozen=True, slots=True)
class StockSheetExportSettings:
    store_slug: str
    enabled: bool
    schedule_kind: str
    weekday: int
    run_time: str
    spreadsheets: tuple[MarketplaceSpreadsheet, ...]
    updated_at: str
    last_attempt_at: str | None
    last_success_at: str | None
    last_error: str | None
    targets: tuple[ExportTarget, ...]

    def target(self, marketplace: str, metric: str) -> ExportTarget:
        for item in self.targets:
            if item.marketplace == marketplace and item.metric == metric:
                return item
        raise KeyError(f"Не настроена выгрузка {marketplace}/{metric}")

    def spreadsheet_url_for(self, marketplace: str) -> str:
        for item in self.spreadsheets:
            if item.marketplace == marketplace:
                return item.spreadsheet_url
        raise KeyError(f"Не настроена Google Таблица для {marketplace}")


def get_settings(store_slug: str) -> StockSheetExportSettings | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM stock_sheet_export_settings WHERE store_slug = ?",
        (store_slug,),
    ).fetchone()
    if row is None:
        conn.close()
        return None
    target_rows = conn.execute(
        """
        SELECT id, marketplace, metric, sheet_name, key_column_name, value_column_name
        FROM stock_sheet_export_targets
        WHERE store_slug = ?
        ORDER BY marketplace, metric, id
        """,
        (store_slug,),
    ).fetchall()
    spreadsheet_rows = conn.execute(
        """
        SELECT marketplace, spreadsheet_url
        FROM stock_sheet_export_marketplaces
        WHERE store_slug = ?
        """,
        (store_slug,),
    ).fetchall()
    conn.close()
    legacy_spreadsheet_url = str(row["spreadsheet_url"] or "")
    spreadsheet_urls = {
        str(spreadsheet["marketplace"]): str(spreadsheet["spreadsheet_url"] or "")
        for spreadsheet in spreadsheet_rows
    }
    return StockSheetExportSettings(
        store_slug=str(row["store_slug"]),
        enabled=bool(row["enabled"]),
        schedule_kind=str(row["schedule_kind"]),
        weekday=int(row["weekday"]),
        run_time=str(row["run_time"]),
        spreadsheets=tuple(
            MarketplaceSpreadsheet(
                marketplace=marketplace,
                spreadsheet_url=spreadsheet_urls.get(marketplace, legacy_spreadsheet_url),
            )
            for marketplace in MARKETPLACES
        ),
        updated_at=str(row["updated_at"]),
        last_attempt_at=row["last_attempt_at"],
        last_success_at=row["last_success_at"],
        last_error=row["last_error"],
        targets=tuple(
            ExportTarget(
                marketplace=str(target["marketplace"]),
                metric=str(target["metric"]),
                sheet_name=str(target["sheet_name"]),
                key_column_name=str(target["key_column_name"]),
                value_column_name=str(target["value_column_name"]),
                id=int(target["id"]),
            )
            for target in target_rows
        ),
    )


def list_settings() -> list[StockSheetExportSettings]:
    conn = get_connection()
    rows = conn.execute("SELECT store_slug FROM stock_sheet_export_settings ORDER BY store_slug").fetchall()
    conn.close()
    return [settings for row in rows if (settings := get_settings(str(row["store_slug"]))) is not None]


def save_settings(settings: StockSheetExportSettings) -> None:
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.execute("BEGIN")
            conn.execute(
                """
                INSERT INTO stock_sheet_export_settings
                    (store_slug, enabled, schedule_kind, weekday, run_time,
                     spreadsheet_url, updated_at, last_attempt_at, last_success_at, last_error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug) DO UPDATE SET
                    enabled = excluded.enabled,
                    schedule_kind = excluded.schedule_kind,
                    weekday = excluded.weekday,
                    run_time = excluded.run_time,
                    spreadsheet_url = excluded.spreadsheet_url,
                    updated_at = excluded.updated_at
                """,
                (
                    settings.store_slug,
                    1 if settings.enabled else 0,
                    settings.schedule_kind,
                    settings.weekday,
                    settings.run_time,
                    settings.spreadsheet_url_for("WB"),
                    settings.updated_at,
                    settings.last_attempt_at,
                    settings.last_success_at,
                    settings.last_error,
                ),
            )
            conn.execute(
                "DELETE FROM stock_sheet_export_marketplaces WHERE store_slug = ?",
                (settings.store_slug,),
            )
            conn.executemany(
                """
                INSERT INTO stock_sheet_export_marketplaces
                    (store_slug, marketplace, spreadsheet_url)
                VALUES (?, ?, ?)
                """,
                [
                    (settings.store_slug, spreadsheet.marketplace, spreadsheet.spreadsheet_url)
                    for spreadsheet in settings.spreadsheets
                ],
            )
            conn.execute(
                "DELETE FROM stock_sheet_export_targets WHERE store_slug = ?",
                (settings.store_slug,),
            )
            conn.executemany(
                """
                INSERT INTO stock_sheet_export_targets
                    (store_slug, marketplace, metric, sheet_name, key_column_name, value_column_name)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        settings.store_slug,
                        target.marketplace,
                        target.metric,
                        target.sheet_name,
                        target.key_column_name,
                        target.value_column_name,
                    )
                    for target in settings.targets
                ],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def record_result(
    store_slug: str,
    attempted_at: str,
    *,
    error: str | None,
) -> None:
    with WRITE_LOCK:
        conn = get_connection()
        conn.execute(
            """
            UPDATE stock_sheet_export_settings
            SET last_attempt_at = ?,
                last_success_at = CASE WHEN ? IS NULL THEN ? ELSE last_success_at END,
                last_error = ?
            WHERE store_slug = ?
            """,
            (attempted_at, error, attempted_at, error, store_slug),
        )
        conn.commit()
        conn.close()
