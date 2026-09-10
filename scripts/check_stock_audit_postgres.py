"""Smoke checks for an isolated disposable database, never the live database."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app import db, stock_total
from app.config import settings
from app.repositories import core
from app.repositories.stock_snapshot import replace_snapshot

url = make_url(settings.database_url or "sqlite://")
if url.database != "rocketbm_stock_audit":
    raise SystemExit("Refusing to run outside the disposable rocketbm_stock_audit database")

NOW = "2026-09-11T00:00:00+00:00"
db.init_db()
db.seed_defaults()
old = {"article": "old", "barcode": "001", "mp_product_id": "123"}
new = {**old, "article": "new", "barcodes": ["001", "000002"]}
db.replace_catalog("rockkiddo", "OZON", [old, new], NOW)
db.upsert_ff_stock("rockkiddo", "old", "AFFLATUS Купавна", 40, NOW, "OZON")
db.upsert_ff_stock("rockkiddo", "new", "AFFLATUS Купавна", 5, NOW, "OZON")
report = db.replace_catalog("rockkiddo", "OZON", [new], NOW)
assert report["reconciled"] == 1
assert db.get_ff_stock_one("rockkiddo", "new", "AFFLATUS Купавна", "OZON") == 45
from app.container import ApplicationContainer
from app.dto.marketplace import Marketplace
from app.dto.stock import ShipmentCommand, SignedStockEntries, SignedStockEntry

container = ApplicationContainer(database_path=lambda: core.DB_PATH)
container.stock.register_fbs_transfer(
    ShipmentCommand(
        store_slug="rockkiddo",
        marketplace=Marketplace.OZON,
        fulfillment="AFFLATUS Купавна",
        entries=SignedStockEntries((SignedStockEntry(code="new", quantity=15),)),
    )
)
replace_snapshot(
    "rockkiddo",
    "OZON",
    {"fbs": {"new": 15}, "fbo": {"new": 10}},
    {"fbs": [("new", "AFFLATUS Купавна", None, 15)]},
    NOW,
)
assert db.get_ff_available_totals("rockkiddo", "AFFLATUS Купавна", "OZON")["new"] == 30
assert db.search_catalog("rockkiddo", "000002", marketplace="OZON")[0]["article"] == "new"
result = next(row for row in stock_total.build_rows(("rockkiddo",)) if row["barcode"] == "001")
assert result["grand_total"] == 55
try:
    replace_snapshot("rockkiddo", "OZON", {"fbs": {"new": 999}}, {"fbs": [("new", None, None, 999)]}, NOW)
except IntegrityError:
    pass
else:
    raise AssertionError("Expected snapshot rollback")
assert db.get_mp_stock_totals("rockkiddo", "OZON", "fbs") == {"new": 15}
with core.get_connection() as conn:
    assert conn.dialect_name == "postgresql"
    assert conn.execute("SELECT current_database() AS name").fetchone()["name"] == "rocketbm_stock_audit"
print(
    json.dumps(
        {
            "database": "isolated PostgreSQL 17",
            "schema": "ok",
            "article_rename": "ok",
            "barcode_aliases": "ok",
            "available": 30,
            "total": 55,
            "rollback": "ok",
        }
    )
)
