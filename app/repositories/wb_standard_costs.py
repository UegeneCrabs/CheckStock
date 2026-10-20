"""Keep the last complete snapshot when any external request fails."""

import json

from app.dto.wb_standard_costs import ProductPhoto, Snapshot, SourceBundle
from app.repositories.core import get_connection


def photos(store: str) -> list[ProductPhoto]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT payload FROM wb_standard_costs_sources WHERE store_slug=?", (store,)
        ).fetchone()
    raw = json.loads(row["payload"]) if row else {}
    result = []
    for card in raw.get("cards", []):
        images = card.get("photos") or []
        if images:
            result.append(
                ProductPhoto(str(card.get("nmID")), images[0].get("tm") or images[0].get("big") or "")
            )
    return result


def load(store: str) -> Snapshot | None:
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
        return build(SourceBundle.model_validate_json(source["payload"]))
    if snapshot is None:
        return None
    snapshot.pop("calculator_inputs", None)
    return Snapshot.model_validate(snapshot)
