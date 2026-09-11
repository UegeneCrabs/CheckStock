"""One-time local JSON migration; never connects to Google Sheets.

python -m scripts.import_yandex_economics --input file.json
Existing source fields and website overrides are preserved on repeated imports.
"""

import argparse
import json
from pathlib import Path

from app import db
from app.dto.yandex_economics import EconomicsValues
from app.repositories import yandex_economics as repository
from app.repositories.yandex_assortment import active_articles
from app.yandex import economics


def import_rows(rows):
    stores = {row["store_slug"] for row in rows}
    economics.bootstrap_1c(stores)
    caches = {store: repository.sources(store) for store in stores}
    active = {store: active_articles(store) for store in stores}
    prepared, skipped = [], 0
    for row in rows:
        store, article, scheme = row["store_slug"], str(row["article"]), row["scheme"]
        if scheme not in ("FBY", "FBS"):
            raise ValueError("Unknown scheme")
        if article not in active[store]:
            skipped += 1
            continue
        incoming = EconomicsValues.model_validate(row["values"]).model_dump(exclude_none=True)
        existing = caches[store].get((article, "initial:" + scheme), {}).get("values", {})
        # Existing 1C purchase/fulfillment take precedence during initial migration.
        merged = {**incoming, **{key: value for key, value in existing.items() if value is not None}}
        prepared.append((store, article, scheme, merged, row.get("source", {})))
    for store, article, scheme, values, source in prepared:
        repository.save_source(store, article, "initial:" + scheme, values)
        repository.save_source(store, article, "migration:" + scheme, source, initial_only=True)
    return {"imported": len(prepared), "skipped_inactive": skipped, **economics.capture_today(stores)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    db.init_db()
    print(json.dumps(import_rows(json.loads(args.input.read_text(encoding="utf-8"))), ensure_ascii=False))
