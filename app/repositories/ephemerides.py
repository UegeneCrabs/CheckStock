"""Bulk reads and optimistic, transactional weekly comment saves."""

from datetime import UTC, datetime

from app.repositories.core import WRITE_LOCK, get_connection


class Conflict(ValueError):
    pass


def comments(stores, start, end):
    if not stores:
        return []
    with get_connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                f"SELECT * FROM ephemerides_comments WHERE store_slug IN ({','.join('?' for _ in stores)}) AND week>=? AND week<=?",
                (*stores, start, end),
            )
        ]


def revisions(key):
    with get_connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM ephemerides_comment_revisions WHERE store_slug=? AND article=? AND week=? AND kind=? ORDER BY version DESC",
                key,
            )
        ]


def save_comment(key, text, expected_version, user):
    stamp = datetime.now(UTC).isoformat()
    version = expected_version + 1
    author = user.full_name or user.login
    values = (version, text, user.id, author, stamp)
    with WRITE_LOCK, get_connection() as conn:
        if expected_version == 0:
            result = conn.execute(
                "INSERT INTO ephemerides_comments (store_slug,article,week,kind,version,text,author_id,author,updated_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(store_slug,article,week,kind) DO NOTHING",
                (*key, *values),
            )
        else:
            result = conn.execute(
                "UPDATE ephemerides_comments SET version=?,text=?,author_id=?,author=?,updated_at=? WHERE store_slug=? AND article=? AND week=? AND kind=? AND version=?",
                (*values, *key, expected_version),
            )
        if result.rowcount != 1:
            raise Conflict("Комментарий уже изменён другим пользователем. Сравните версии перед сохранением.")
        conn.execute(
            "INSERT INTO ephemerides_comment_revisions (store_slug,article,week,kind,version,text,author_id,author,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (*key, *values),
        )
        conn.commit()
    return dict(
        zip(
            ("store_slug", "article", "week", "kind", "version", "text", "author_id", "author", "updated_at"),
            (*key, *values),
            strict=True,
        )
    )


def dated_sources(stores, start, end):
    if not stores:
        return [], []
    marks = ",".join("?" for _ in stores)
    with get_connection() as conn:
        prices = [
            dict(row)
            for row in conn.execute(
                f"SELECT * FROM unit_economics_1c_wb_daily_prices WHERE store_slug IN ({marks}) AND day>=? AND day<=? ORDER BY day",
                (*stores, start, end),
            )
        ]
        # Project only the tiny reference object, never transfer the raw audit archive.
        reference = (
            "payload_json::jsonb #>> '{source,raw,reference}'"
            if conn.dialect_name == "postgresql"
            else "json_extract(payload_json, '$.source.raw.reference')"
        )
        goals = [
            dict(row)
            for row in conn.execute(
                f"SELECT store_slug,article,day,{reference} AS reference_json FROM economics_daily WHERE marketplace='WB' AND store_slug IN ({marks}) AND day>=? AND day<=? ORDER BY day",
                (*stores, start, end),
            )
        ]
    return prices, goals


def transit(stores):
    if not stores:
        return {}
    with get_connection() as conn:
        return {
            (row["store_slug"], row["nm_id"]): dict(row)
            for row in conn.execute(
                f"SELECT * FROM wb_customer_transit WHERE store_slug IN ({','.join('?' for _ in stores)})",
                stores,
            )
        }


def replace_transit(store, snapshot):
    stamp = datetime.now(UTC).isoformat()
    # Atomic replacement, only after the complete API report was validated.
    with WRITE_LOCK, get_connection() as conn:
        conn.execute("DELETE FROM wb_customer_transit WHERE store_slug=?", (store,))
        conn.executemany(
            "INSERT INTO wb_customer_transit (store_slug,nm_id,from_customer,to_customer,updated_at) VALUES (?,?,?,?,?)",
            [
                (store, article, snapshot.from_customer[article], snapshot.to_customer[article], stamp)
                for article in snapshot.from_customer
            ],
        )
        conn.commit()
