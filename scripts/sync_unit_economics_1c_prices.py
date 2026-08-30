"""Вручную обновить и проверить цены WB для юнит-экономики 1С."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app import unit_economics_1c_prices as price_sync
from app.stores import STORES


def _source(row: dict) -> str:
    if row.get("customer_price_with_spp") is None:
        return "нет цены"
    window_days = row.get("customer_price_window_days")
    if window_days:
        return f"orders:{window_days}d"
    return "storefront"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store",
        action="append",
        choices=tuple(STORES),
        help="Slug кабинета. Можно повторить несколько раз; по умолчанию — все.",
    )
    parser.add_argument(
        "--show",
        type=int,
        default=10,
        help="Сколько последних строк показать из базы для каждого кабинета; по умолчанию 10.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )

    stores = tuple(dict.fromkeys(args.store or STORES))
    show_limit = max(args.show, 0)
    db.init_db()
    nm_ids = price_sync._catalog_nm_ids(stores)
    batch_size = price_sync.wb_api.STOREFRONT_MAX_BATCH_SIZE
    batch_count = (len(nm_ids) + batch_size - 1) // batch_size if nm_ids else 0

    print(
        f"Синхронизация цен WB: кабинетов={len(stores)}, "
        f"уникальных активных товаров={len(nm_ids)}, "
        f"товаров в пачке={batch_size}, запросов={batch_count}, "
        f"пауза={price_sync.wb_api.STOREFRONT_BATCH_PAUSE_SECONDS:.1f} с, "
        "режим=только цена с СПП",
        flush=True,
    )
    sync_started_at = datetime.now(UTC)
    started = time.monotonic()
    report = price_sync.sync_stores(
        stores,
        load_retail_prices=False,
        storefront_batch_size=batch_size,
    )
    elapsed = time.monotonic() - started
    latest = db.get_unit_economics_1c_latest_daily_prices(stores)

    for store_slug in stores:
        result = report.get(store_slug) or {}
        status = str(result.get("status") or "error").upper()
        print(
            f"\n[{store_slug}] {status}: сохранено={result.get('rows', 0)}, "
            f"витрина={result.get('storefront_rows', 0)}, "
            f"не обновлено={result.get('unresolved_rows', 0)}",
            flush=True,
        )
        print(
            f"  каталог={result.get('catalog_products', 0)}, "
            f"WB вернул={result.get('storefront_returned_products', 0)}, "
            f"с ценой={result.get('storefront_priced_products', 0)}, "
            f"не вернул={result.get('storefront_omitted_products', 0)}, "
            f"без цены={result.get('storefront_without_price_products', 0)}, "
            f"без остатка={result.get('storefront_out_of_stock_products', 0)}",
            flush=True,
        )
        if result.get("error"):
            print(f"  {result['error']}", flush=True)

        rows = [
            row
            for row in latest
            if row.get("store_slug") == store_slug
            and str(row.get("updated_at") or "") >= sync_started_at.isoformat()
        ]
        print(f"  Строки, записанные именно этим запуском: {len(rows)}", flush=True)
        for row in rows[:show_limit]:
            print(
                f"  {row.get('article')}: "
                f"с СПП={row.get('customer_price_with_spp')} RUB, "
                f"день={row.get('day')}, источник СПП={_source(row)}",
                flush=True,
            )
        if len(rows) > show_limit:
            print(f"  ... и ещё {len(rows) - show_limit} строк", flush=True)

    print(f"\nВремя выполнения: {elapsed:.1f} с", flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if all(result.get("ok") for result in report.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
