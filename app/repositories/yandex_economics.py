import json
import uuid
from datetime import UTC, datetime

from app.repositories.core import WRITE_LOCK, get_connection


def now():
    return datetime.now(UTC).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def settings(store, article, scheme):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM yandex_economics_settings WHERE store_slug=? AND article=? AND scheme=?",
            (store, article, scheme),
        ).fetchone()
    return {**dict(row), "values": json.loads(row["payload_json"])} if row else {"revision": 0, "values": {}}


def all_settings(store):
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM yandex_economics_settings WHERE store_slug=?", (store,)).fetchall()
    return {
        (row["article"], row["scheme"]): {**dict(row), "values": json.loads(row["payload_json"])}
        for row in rows
    }


def save_settings(store, article, scheme, changes, revision, actor):
    timestamp = now()
    with WRITE_LOCK, get_connection() as conn:
        row = conn.execute(
            "SELECT revision,payload_json FROM yandex_economics_settings WHERE store_slug=? AND article=? AND scheme=?",
            (store, article, scheme),
        ).fetchone()
        old_revision = row["revision"] if row else 0
        if old_revision != revision:
            raise ValueError("Параметры уже изменены. Обновите карточку перед сохранением.")
        before = json.loads(row["payload_json"]) if row else {}
        after = {**before, **changes}
        after = {key: value for key, value in after.items() if value is not None}
        result = conn.execute(
            """INSERT INTO yandex_economics_settings
               (store_slug,article,scheme,revision,payload_json,updated_at,updated_by)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(store_slug,article,scheme) DO UPDATE SET
               revision=excluded.revision,payload_json=excluded.payload_json,
               updated_at=excluded.updated_at,updated_by=excluded.updated_by
               WHERE yandex_economics_settings.revision=?""",
            (store, article, scheme, revision + 1, encode(after), timestamp, actor, revision),
        )
        if result.rowcount != 1:
            raise ValueError("Параметры уже изменены. Обновите карточку.")
        conn.execute(
            "INSERT INTO yandex_economics_audit (id,store_slug,article,scheme,payload_json,created_at,actor) VALUES (?,?,?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                store,
                article,
                scheme,
                encode({"before": before, "after": after}),
                timestamp,
                actor,
            ),
        )
        conn.commit()
    return settings(store, article, scheme)


def source(store, article, name):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT payload_json,updated_at FROM yandex_economics_sources WHERE store_slug=? AND article=? AND source=?",
            (store, article, name),
        ).fetchone()
    return {"values": json.loads(row["payload_json"]), "updated_at": row["updated_at"]} if row else {}


def sources(store):
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM yandex_economics_sources WHERE store_slug=?", (store,)).fetchall()
    return {
        (row["article"], row["source"]): {
            "values": json.loads(row["payload_json"]),
            "updated_at": row["updated_at"],
        }
        for row in rows
    }


def save_source(store, article, source, values, *, updated_at=None, initial_only=False):
    timestamp = updated_at or now()
    conflict = (
        "DO NOTHING"
        if initial_only
        else "DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at"
    )
    with WRITE_LOCK, get_connection() as conn:
        conn.execute(
            "INSERT INTO yandex_economics_sources (store_slug,article,source,payload_json,updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(store_slug,article,source) " + conflict,
            (store, article, source, encode(values), timestamp),
        )
        conn.commit()


def save_sources(store, entries, *, updated_at=None):
    """Publish one store's category checks atomically, without touching manual settings."""
    timestamp = updated_at or now()
    rows = [(store, article, source, encode(values), timestamp) for article, source, values in entries]
    with WRITE_LOCK, get_connection() as conn:
        conn.executemany(
            "INSERT INTO yandex_economics_sources (store_slug,article,source,payload_json,updated_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(store_slug,article,source) DO UPDATE SET "
            "payload_json=excluded.payload_json,updated_at=excluded.updated_at",
            rows,
        )
        conn.commit()


def save_day(store, article, scheme, day, payload):
    with WRITE_LOCK, get_connection() as conn:
        conn.execute(
            """INSERT INTO yandex_economics_daily (store_slug,article,scheme,day,payload_json,captured_at)
               VALUES (?,?,?,?,?,?) ON CONFLICT(store_slug,article,scheme,day) DO NOTHING""",
            (store, article, scheme, day, encode(payload), now()),
        )
        conn.commit()


def history(store, start, end):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM yandex_economics_daily WHERE store_slug=? AND day>=? AND day<=? ORDER BY day",
            (store, start, end),
        ).fetchall()
    return [{**dict(row), "data": json.loads(row["payload_json"])} for row in rows]


def audit(store, article):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM yandex_economics_audit WHERE store_slug=? AND article=? ORDER BY created_at DESC LIMIT 30",
            (store, article),
        ).fetchall()
    return [{**dict(row), "changes": json.loads(row["payload_json"])} for row in rows]
