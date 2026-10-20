"""Keep the last complete snapshot when any external request fails."""

import json

from app.repositories.core import WRITE_LOCK, get_connection


def photos(store: str) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT payload FROM wb_standard_costs_sources WHERE store_slug=?", (store,)
        ).fetchone()
    raw = json.loads(row["payload"]) if row else {}
    result = {}
    for card in raw.get("cards", []):
        images = card.get("photos") or []
        if images:
            result[str(card.get("nmID"))] = images[0].get("tm") or images[0].get("big") or ""
    return result


def load(store: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT payload FROM wb_standard_costs_snapshots WHERE store_slug=?", (store,)
        ).fetchone()
    snapshot = json.loads(row["payload"]) if row else None
    if snapshot and snapshot.get("version", 0) < 5:
        # Recover precision from saved API responses, without network calls or writes.
        from app.economics.wb.standard_costs import build

        with get_connection() as conn:
            source = conn.execute(
                "SELECT payload FROM wb_standard_costs_sources WHERE store_slug=?", (store,)
            ).fetchone()
        if not source:
            raise ValueError("Для обновления расчёта требуется повторная загрузка источников")
        snapshot = build(json.loads(source["payload"]))
    return snapshot


def save(store: str, payload: dict, *, raw: dict | None = None) -> None:
    with WRITE_LOCK, get_connection() as conn:
        conn.execute(
            "INSERT INTO wb_standard_costs_snapshots (store_slug,payload,updated_at) VALUES (?,?,?) "
            "ON CONFLICT(store_slug) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
            (store, json.dumps(payload, ensure_ascii=False, allow_nan=False), payload["updated_at"]),
        )
        if raw is not None:
            conn.execute(
                "INSERT INTO wb_standard_costs_sources (store_slug,payload,updated_at) VALUES (?,?,?) "
                "ON CONFLICT(store_slug) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                (store, json.dumps(raw, ensure_ascii=False, allow_nan=False), payload["updated_at"]),
            )
        conn.commit()
