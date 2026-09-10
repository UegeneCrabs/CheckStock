"""Current 1C source values for Yandex, including offers not yet in the catalog."""

from app.repositories.core import WRITE_LOCK, get_connection


def get_values(store_slug: str) -> dict[str, dict]:
    with get_connection() as connection:
        return {
            row["article"]: dict(row)
            for row in connection.execute(
                "SELECT * FROM unit_economics_yandex_source_values WHERE store_slug=?",
                (store_slug,),
            )
        }


def replace_values(rows: list[dict], team_commissions: dict[str, float], synced_at: str) -> int:
    columns = (
        "store_slug",
        "article",
        "purchase_price",
        "fulfillment_cost",
        "team_commission_percent",
        "manager",
        "tag_raw",
        "goal_week",
        "goal_day",
        "stock_status",
        "stock_end_week",
        "supplier_external_raw",
        "abc_code",
        "fact_sales",
        "plan_sales",
        "source_sheet_id",
        "source_sheet_title",
        "source_row",
        "synced_at",
    )
    with WRITE_LOCK, get_connection() as connection:
        connection.execute("DELETE FROM unit_economics_yandex_source_values")
        connection.executemany(
            f"INSERT INTO unit_economics_yandex_source_values ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            [[row.get(column) if column != "synced_at" else synced_at for column in columns] for row in rows],
        )
        connection.executemany(
            """
            INSERT INTO unit_economics_1c_cabinet_settings
                (store_slug, marketplace, team_commission_percent, updated_at, updated_by_user_id, updated_by_name)
            VALUES (?, 'YANDEX MARKET', ?, ?, 0, 'Google Sheets')
            ON CONFLICT(store_slug, marketplace) DO UPDATE SET
                team_commission_percent=excluded.team_commission_percent,
                updated_at=excluded.updated_at,
                updated_by_user_id=excluded.updated_by_user_id,
                updated_by_name=excluded.updated_by_name
            """,
            [(slug, value, synced_at) for slug, value in team_commissions.items()],
        )
        connection.commit()
    return len(rows)
