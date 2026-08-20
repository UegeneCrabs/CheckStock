from app.repositories.core import WRITE_LOCK, get_connection


def _manual_supply(row) -> dict:
    return {
        "id": int(row["id"]),
        "store_slug": str(row["store_slug"]),
        "delivery_at": str(row["delivery_at"]),
        "origin": str(row["origin"]),
        "destination": str(row["destination"]),
        "supply_type": str(row["supply_type"]),
        "ready": bool(row["ready"]),
        "created_by_name": str(row["created_by_name"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def list_manual_supplies(store_slugs: tuple[str, ...]) -> list[dict]:
    if not store_slugs:
        return []
    placeholders = ", ".join("?" for _ in store_slugs)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT id, store_slug, delivery_at, origin, destination, supply_type, ready,
                   created_by_name, created_at, updated_at
              FROM manual_supplies
             WHERE ready = 0 AND store_slug IN ({placeholders})
             ORDER BY delivery_at ASC, id ASC
            """,
            store_slugs,
        ).fetchall()
    return [_manual_supply(row) for row in rows]


def get_manual_supply(supply_id: int) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, store_slug, delivery_at, origin, destination, supply_type, ready,
                   created_by_name, created_at, updated_at
              FROM manual_supplies
             WHERE id = ?
            """,
            (supply_id,),
        ).fetchone()
    return _manual_supply(row) if row is not None else None


def create_manual_supply(
    store_slug: str,
    delivery_at: str,
    origin: str,
    destination: str,
    supply_type: str,
    ready: bool,
    created_by_user_id: int | None,
    created_by_name: str,
    now: str,
) -> dict:
    with WRITE_LOCK, get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO manual_supplies
                (store_slug, delivery_at, origin, destination, supply_type, ready,
                 created_by_user_id, created_by_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                store_slug,
                delivery_at,
                origin,
                destination,
                supply_type,
                int(ready),
                created_by_user_id,
                created_by_name,
                now,
                now,
            ),
        )
        supply_id = cursor.lastrowid
        connection.commit()
    result = get_manual_supply(supply_id)
    if result is None:
        raise RuntimeError("Созданная поставка не найдена")
    return result


def update_manual_supply(
    supply_id: int,
    store_slug: str,
    delivery_at: str,
    origin: str,
    destination: str,
    supply_type: str,
    ready: bool,
    now: str,
) -> dict | None:
    with WRITE_LOCK, get_connection() as connection:
        result = connection.execute(
            """
            UPDATE manual_supplies
               SET store_slug = ?, delivery_at = ?, origin = ?, destination = ?, supply_type = ?,
                   ready = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                store_slug,
                delivery_at,
                origin,
                destination,
                supply_type,
                int(ready),
                now,
                supply_id,
            ),
        )
        connection.commit()
    return get_manual_supply(supply_id) if result.rowcount else None


def set_manual_supply_ready(supply_id: int, ready: bool, now: str) -> dict | None:
    with WRITE_LOCK, get_connection() as connection:
        result = connection.execute(
            "UPDATE manual_supplies SET ready = ?, updated_at = ? WHERE id = ?",
            (int(ready), now, supply_id),
        )
        connection.commit()
    return get_manual_supply(supply_id) if result.rowcount else None


def delete_manual_supply(supply_id: int) -> bool:
    with WRITE_LOCK, get_connection() as connection:
        result = connection.execute("DELETE FROM manual_supplies WHERE id = ?", (supply_id,))
        connection.commit()
    return bool(result.rowcount)
