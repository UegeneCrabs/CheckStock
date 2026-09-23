"""Вручную обновить и проверить цены WB для юнит-экономики 1С."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import db
from app.core.stores import STORES
from app.economics.wb import prices as price_sync
from app.jobs import locks
from app.jobs.tracking import run_tracked


def _source(row: dict) -> str:
    if row.get("customer_price_with_spp") is None:
        return "нет цены"
    window_days = row.get("customer_price_window_days")
    if window_days:
        return f"orders:{window_days}d"
    return "витрина / последняя СПП"


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
    parser.add_argument(
        "--prepare-session",
        action="store_true",
        help="Перед выгрузкой обновить анонимный сеанс в отдельном Яндекс Браузере (Windows).",
    )
    parser.add_argument(
        "--storefront-only",
        action="store_true",
        help="Обновить только цены с СПП и WB Кошельком, без запроса цены продавца.",
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
        f"уникальных товаров каталога={len(nm_ids)}, "
        f"товаров в пачке={batch_size}, запросов={batch_count}, "
        f"пауза={price_sync.wb_api.STOREFRONT_BATCH_PAUSE_SECONDS:.1f} с, "
        + ("режим=СПП и WB Кошелёк" if args.storefront_only else "режим=все цены"),
        flush=True,
    )
    sync_started_at = datetime.now(UTC)
    started = time.monotonic()

    def collect():
        if args.prepare_session:
            from scripts.parsers.prepare_wb_storefront_session import main as prepare_session

            if prepare_session([]):
                raise RuntimeError("Сеанс WB не подготовлен; цены в БД не изменены.")
        return price_sync.sync_stores(
            stores,
            load_retail_prices=not args.storefront_only,
            storefront_batch_size=batch_size,
        )

    # Both price jobs write the same daily rows. Avoid simultaneous CLI/web runs.
    job = "unit_economics_1c_wallet_sync" if args.storefront_only else "unit_economics_1c_sync"
    other_job = "unit_economics_1c_sync" if args.storefront_only else "unit_economics_1c_wallet_sync"
    try:
        with locks.hold(other_job):
            report = run_tracked(job, "manual", collect)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    elapsed = time.monotonic() - started
    latest = db.get_unit_economics_1c_latest_daily_prices(stores)

    for store_slug in stores:
        result = report.get(store_slug) or {}
        status = str(result.get("status") or "error").upper()
        print(
            f"\n[{store_slug}] {status}: сохранено={result.get('rows', 0)}, "
            f"витрина={result.get('storefront_rows', 0)}, "
            f"из них по последней СПП={result.get('estimated_spp_rows', 0)}, "
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
                f"с WB Кошельком={row.get('customer_price_with_wallet')} RUB, "
                f"день={row.get('day')}, источник СПП={_source(row)}",
                flush=True,
            )
        if len(rows) > show_limit:
            print(f"  ... и ещё {len(rows) - show_limit} строк", flush=True)

    print(f"\nВремя выполнения: {elapsed:.1f} с", flush=True)
    print(
        json.dumps(
            {
                store: {key: value for key, value in result.items() if key != "refreshed_prices"}
                for store, result in report.items()
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0 if all(result.get("ok") for result in report.values()) else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
