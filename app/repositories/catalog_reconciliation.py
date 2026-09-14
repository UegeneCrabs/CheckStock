"""Move current balances after an unambiguous marketplace article rename.

Operation history is deliberately immutable. The alias records the relationship
between the historical article and its current seller offer.
"""

from app.stock.catalog_identity import barcodes


def reconcile_renames(conn, store, marketplace, existing, incoming, now):
    identity_field = {"OZON": "mp_product_id", "YANDEX MARKET": "mp_sku"}.get(marketplace)
    if identity_field is None:
        return set()
    current = {str(item["article"]): item for item in incoming if item.get("article")}
    by_identity = {}
    for article, item in current.items():
        identity = str(item.get(identity_field) or "")
        if identity:
            by_identity.setdefault(identity, []).append(article)
    merged = set()
    for old_article, old in existing.items():
        if old_article in current:
            continue
        identity = str(old[identity_field] or "")
        targets = by_identity.get(identity, [])
        if not identity or len(targets) != 1:
            continue
        target = targets[0]
        if not old["barcode"] or old["barcode"] not in barcodes(current[target]):
            continue
        _move_balances(conn, store, marketplace, old_article, target)
        conn.execute(
            "INSERT INTO catalog_barcodes(stock_item_id,barcode) "
            "SELECT new.id,cb.barcode FROM catalog_barcodes cb "
            "JOIN stock_items old ON old.id=cb.stock_item_id "
            "JOIN stock_items new ON new.store_slug=old.store_slug AND new.marketplace=old.marketplace "
            "WHERE old.store_slug=? AND old.marketplace=? AND old.article=? AND new.article=? "
            "ON CONFLICT(stock_item_id,barcode) DO NOTHING",
            (store, marketplace, old_article, target),
        )
        conn.execute(
            "INSERT INTO catalog_barcodes(stock_item_id,barcode) "
            "SELECT id,? FROM stock_items WHERE store_slug=? AND marketplace=? AND article=? "
            "ON CONFLICT(stock_item_id,barcode) DO NOTHING",
            (old["barcode"], store, marketplace, target),
        )
        conn.execute(
            "INSERT INTO catalog_article_aliases "
            "(store_slug,marketplace,article,target_article,identity,updated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(store_slug,marketplace,article) DO UPDATE SET "
            "target_article=excluded.target_article,identity=excluded.identity,updated_at=excluded.updated_at",
            (store, marketplace, old_article, target, identity_field + ":" + identity, now),
        )
        conn.execute(
            "UPDATE catalog_article_aliases SET target_article=? "
            "WHERE store_slug=? AND marketplace=? AND target_article=?",
            (target, store, marketplace, old_article),
        )
        merged.add(old_article)
    return merged


def _move_balances(conn, store, marketplace, source, target):
    for table, dimensions in (
        ("ff_stock", ["fulfillment"]),
        ("trash_stock", ["fulfillment"]),
        ("ff_import_snapshots", ["fulfillment", "source_type", "source_key"]),
    ):
        lock = " FOR UPDATE" if conn.dialect_name == "postgresql" else ""
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE store_slug=? AND marketplace=? AND article=?{lock}",
            (store, marketplace, source),
        ).fetchall()
        for row in rows:
            predicate = " AND ".join(f"{name}=?" for name in dimensions)
            params = (store, marketplace, target, *(row[name] for name in dimensions))
            match = conn.execute(
                f"SELECT id FROM {table} WHERE store_slug=? AND marketplace=? "
                f"AND article=? AND {predicate}{lock}",
                params,
            ).fetchone()
            if match:
                conn.execute(
                    f"UPDATE {table} SET quantity=quantity+? WHERE id=?", (row["quantity"], match["id"])
                )
                conn.execute(f"DELETE FROM {table} WHERE id=?", (row["id"],))
            else:
                conn.execute(f"UPDATE {table} SET article=? WHERE id=?", (target, row["id"]))
    for direction in ("from", "to"):
        conn.execute(
            f"UPDATE ff_transit_items SET {direction}_article=? WHERE {direction}_article=? "
            f"AND batch_id IN (SELECT id FROM ff_transit_batches WHERE store_slug=? AND {direction}_marketplace=?)",
            (target, source, store, marketplace),
        )
    for table in ("mp_stock", "mp_warehouse_stock"):
        conn.execute(
            f"DELETE FROM {table} WHERE store_slug=? AND marketplace=? AND article=?",
            (store, marketplace, source),
        )
