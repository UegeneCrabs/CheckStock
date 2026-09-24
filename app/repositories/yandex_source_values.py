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


def list_values() -> list[dict]:
    """Return all retained YM article-to-store bindings.

    The public cost-price export contains an article and a price, but no
    marketplace cabinet.  The bindings are maintained in this local table
    and are deliberately reused when the price-only export is refreshed.
    """

    with get_connection() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM unit_economics_yandex_source_values "
                "ORDER BY store_slug, article"
            )
        ]


def replace_values(rows: list[dict], synced_at: str) -> int:
    columns = (
        "store_slug",
        "article",
        "purchase_price",
        "fulfillment_cost",
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
        # A manually entered purchase price fills a source-sheet gap. Keep it
        # on a normal refresh until the source itself starts providing that
        # article; otherwise a scheduled sync would silently make COGS
        # incomplete again.
        manual_rows = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM unit_economics_yandex_source_values "
                "WHERE source_sheet_id=0 AND source_sheet_title='Ручная корректировка'"
            )
        ]
        incoming_keys = {(str(row.get("store_slug")), str(row.get("article"))) for row in rows}
        retained_manual_rows = [
            row
            for row in manual_rows
            if (str(row["store_slug"]), str(row["article"])) not in incoming_keys
        ]
        connection.execute("DELETE FROM unit_economics_yandex_source_values")
        all_rows = [*rows, *retained_manual_rows]
        connection.executemany(
            f"INSERT INTO unit_economics_yandex_source_values ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            [
                [
                    row.get(column)
                    if column != "synced_at" or row.get("source_sheet_id") == 0
                    else synced_at
                    for column in columns
                ]
                for row in all_rows
            ],
        )
        connection.commit()
    return len(all_rows)
