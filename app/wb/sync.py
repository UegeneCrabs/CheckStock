"""
Синхронизация остатков FBS/FBO с Wildberries по всем магазинам.

Логика:
1. Для каждого магазина берём его каталог (артикул/баркод) из stock_items.
2. FBS: получаем ВСЕ склады продавца (/api/v3/warehouses), по каждому
   параллельно тянем остатки, сопоставляем склад с одним из наших
   фулфилментов по названию (без учёта регистра/пробелов) — так заполняется
   fbs_ff_stock (остаток по каждому ФФ отдельно, для переключателя на
   странице). Сумма по всем складам сразу — это "Общее", сохраняется в
   fbs_stock, как и раньше.
3. FBO: получаем отчёт по остаткам на складах WB, суммируем по баркоду
   (тотал по товару, как просили), сопоставляем баркод -> артикул,
   сохраняем в таблицу fbo_stock.

Если у магазина нет токена (secrets/wb_tokens.json) — магазин просто
пропускается, без падения всего процесса.

Параллельность: FBS и FBO по каждому магазину, все магазины между собой, и
теперь ещё и склады продавца внутри одного FBS-запроса — везде это чисто
сетевые (blocking I/O) запросы к WB, друг другу не мешают, а лимиты WB
считаются на сам запрос, а не на магазин/кабинет в целом. Запись в SQLite
при этом сериализована отдельным локом (_DB_LOCK) — сама WB-часть (самая
долгая) всё равно выполняется параллельно, а короткие апсерты в БД просто
не дают потокам одновременно писать в один файл.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from app import db
from app.stores import STORES
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens

logger = logging.getLogger("checkstock.sync")

# Общий лок на запись в SQLite (см. db.WRITE_LOCK) — сериализует фактическую
# запись между потоками синхронизации и другими частями приложения (например,
# ручной загрузкой остатков на ФФ), не мешая при этом самим запросам к WB.
_DB_LOCK = db.WRITE_LOCK

# Технические/транзитные склады WB — не считаются "в продаже", поэтому
# исключаются из тотала FBO (но сохраняются в детализацию по складам).
EXCLUDED_FBO_WAREHOUSES = {
    "Электросталь",
    "Невинномысск",
    "Краснодар",
    "СЦ Шушары",
    "Склад СПБ Шушары Московское",
    "Санкт-Петербург Уткина Заводь",
    "Рязань (Тюшевское)",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store_label(store_slug: str) -> str:
    """Имя магазина для логов — по нему сразу видно, чей ключ настроен не так."""
    store = STORES.get(store_slug) or {}
    return store.get("name") or store_slug.upper()


def _error_message(e: Exception) -> str:
    """Человекочитаемое сообщение: для известных ошибок WB — их .friendly,
    для всего непредвиденного (баг, неожиданный ответ и т.п.) — хотя бы тип
    исключения, чтобы было понятно, что это не про WB, а про сам код."""
    if isinstance(e, wb_api.WBApiError):
        return e.friendly
    return f"непредвиденная ошибка ({type(e).__name__}): {e}"


def _normalize_ff_name(name: str) -> str:
    """Нормализация названия для сопоставления 'ФулСервис Подольск' и
    'Фулсервис Подольск' (реальные варианты из WB) как одного и того же ФФ."""
    return " ".join((name or "").strip().casefold().split())


def sync_store_fbs(store_slug: str) -> int:
    """Тянет остатки FBS по ВСЕМ складам продавца в WB (параллельно), сопоставляет
    каждый склад с одним из наших фулфилментов по названию и сохраняет:
    - fbs_ff_stock — остаток по каждому фулфилменту отдельно (переключатель ФФ);
    - fbs_stock — тотал по товару по ВСЕМ складам продавца сразу ("Общее").
    Склад, название которого не совпало ни с одним известным ФФ, не теряется —
    просто сохраняется под своим собственным именем из WB (не появится в
    выпадающем списке ФФ на сайте, но участвует в "Общее").
    Возвращает количество обновлённых артикулов."""
    token = wb_tokens.get_token(store_slug)
    catalog = db.get_catalog_items(store_slug)
    if not catalog:
        return 0

    warehouses = wb_api.get_own_warehouses(token)
    if not warehouses:
        raise wb_api.WBApiError(None, detail="в личном кабинете WB не настроен ни один склад FBS")

    known_by_norm = {_normalize_ff_name(name): name for name in db.get_fulfillments()}
    barcodes = [item["barcode"] for item in catalog]

    # Остатки по каждому складу продавца тянем параллельно — это независимые запросы.
    stock_by_warehouse: dict[int, dict[str, int]] = {}
    warehouse_errors: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(warehouses))) as executor:
        future_to_wh = {
            executor.submit(wb_api.get_fbs_stock, token, wh["id"], barcodes): wh
            for wh in warehouses
        }
        for future in as_completed(future_to_wh):
            wh = future_to_wh[future]
            try:
                stock_by_warehouse[wh["id"]] = future.result()
            except Exception as e:
                warehouse_errors[wh["id"]] = _error_message(e)
                logger.warning(
                    "FBS: склад %s (%s) магазина %s не ответил: %s",
                    wh["id"], wh.get("name"), store_slug, warehouse_errors[wh["id"]],
                )

    if warehouse_errors and len(warehouse_errors) == len(warehouses):
        first_err = next(iter(warehouse_errors.values()))
        raise wb_api.WBApiError(
            None, detail=f"не удалось получить остатки FBS ни по одному складу продавца: {first_err}"
        )

    now = _now()

    # Склад WB -> имя фулфилмента (если совпало) или собственное имя склада WB (если нет)
    ff_label_by_warehouse: dict[int, str] = {}
    for wh in warehouses:
        norm = _normalize_ff_name(wh["name"])
        ff_label_by_warehouse[wh["id"]] = known_by_norm.get(norm, wh["name"])

    # На случай, если два склада внормализуются в один и тот же ФФ — не падаем на
    # UNIQUE(store_slug, fulfillment), а суммируем остатки под одной меткой.
    totals: dict[str, int] = {item["article"]: 0 for item in catalog}
    ff_quantities: dict[tuple[str, str], int] = {}
    seen_labels: dict[str, int] = {}
    ff_map_entries: list[tuple[str, int, str, str]] = []

    for wh in warehouses:
        label = ff_label_by_warehouse[wh["id"]]
        if label not in seen_labels:
            seen_labels[label] = wh["id"]
            ff_map_entries.append((label, wh["id"], wh["name"], now))

        stock_by_barcode = stock_by_warehouse.get(wh["id"], {})
        for item in catalog:
            qty = stock_by_barcode.get(item["barcode"], 0)
            totals[item["article"]] += qty
            key = (item["article"], label)
            ff_quantities[key] = ff_quantities.get(key, 0) + qty

    ff_stock_entries = [
        (article, label, qty, now) for (article, label), qty in ff_quantities.items()
    ]

    # Сеть и подготовка данных (выше) — вне лока, пишем в БД одним махом под ним.
    # Везде явно указываем маркетплейс WB: остатки Ozon лежат в тех же таблицах
    # и не должны пересечься с нашими.
    with _DB_LOCK:
        db.replace_ff_warehouse_map(store_slug, ff_map_entries)
        db.replace_mp_warehouse_stock(
            store_slug, "WB", "fbs",
            [(article, fulfillment, None, quantity, updated_at)
             for article, fulfillment, quantity, updated_at in ff_stock_entries],
        )
        for item in catalog:
            db.upsert_mp_stock(
                store_slug, item["article"], "WB", "fbs", totals[item["article"]], now
            )

    return len(catalog)


def sync_store_fbo(store_slug: str) -> int:
    token = wb_tokens.get_token(store_slug)
    catalog = db.get_catalog_items(store_slug)
    if not catalog:
        return 0

    # {(barcode, warehouseName): quantity}
    by_warehouse = wb_api.get_fbo_stock_by_warehouse(token)

    # сгруппировать по баркоду, чтобы быстро находить склады конкретного товара
    barcode_to_warehouses: dict[str, list[tuple[str, int]]] = {}
    for (barcode, warehouse), quantity in by_warehouse.items():
        barcode_to_warehouses.setdefault(barcode, []).append((warehouse, quantity))

    now = _now()
    warehouse_entries: list[tuple[str, str, int, str]] = []
    totals: list[tuple[str, int]] = []  # (article, total)
    updated = 0

    for item in catalog:
        entries = barcode_to_warehouses.get(item["barcode"], [])

        # транзитные склады не считаются "в продаже" — исключаем и из тотала,
        # и из детализации (в БД вообще не попадают)
        sellable_entries = [(wh, qty) for wh, qty in entries if wh not in EXCLUDED_FBO_WAREHOUSES]

        total = sum(qty for _, qty in sellable_entries)
        totals.append((item["article"], total))

        for warehouse, qty in sellable_entries:
            warehouse_entries.append((item["article"], warehouse, qty, now))

        updated += 1

    # Сеть и подготовка данных (выше) — вне лока, пишем в БД одним махом под ним
    with _DB_LOCK:
        for article, total in totals:
            db.upsert_mp_stock(store_slug, article, "WB", "fbo", total, now)
        db.replace_mp_warehouse_stock(
            store_slug, "WB", "fbo",
            [(article, warehouse, None, quantity, updated_at)
             for article, warehouse, quantity, updated_at in warehouse_entries],
        )
    return updated


def sync_all() -> dict:
    """Синхронизирует FBS и FBO по всем магазинам, у которых есть токен.

    FBS и FBO по каждому магазину и все магазины между собой синхронизируются
    параллельно (в пуле потоков) — это независимые сетевые запросы к WB,
    запись в SQLite при этом сериализована отдельным локом (см. _DB_LOCK).

    Возвращает структурированный отчёт по каждому магазину:
        {
            "rimili": {
                "token": True,
                "fbs": {"ok": True, "count": 3} | {"ok": False, "error": "..."},
                "fbo": {"ok": True, "count": 3} | {"ok": False, "error": "..."},
            },
            "tris": {"token": False, "fbs": None, "fbo": None},
            ...
        }
    """
    report: dict = {}
    active_slugs = []

    for slug in STORES:
        if not wb_tokens.has_token(slug):
            report[slug] = {"token": False, "fbs": None, "fbo": None}
        else:
            report[slug] = {"token": True, "fbs": None, "fbo": None}
            active_slugs.append(slug)

    if not active_slugs:
        return report

    jobs = {
        "fbs": sync_store_fbs,
        "fbo": sync_store_fbo,
    }

    # 2 задачи (FBS+FBO) на каждый магазин с токеном — все выполняются одновременно.
    with ThreadPoolExecutor(max_workers=max(2, len(active_slugs) * 2)) as executor:
        future_to_task = {
            executor.submit(func, slug): (slug, kind)
            for slug in active_slugs
            for kind, func in jobs.items()
        }

        for future in as_completed(future_to_task):
            slug, kind = future_to_task[future]
            try:
                count = future.result()
                report[slug][kind] = {"ok": True, "count": count}
                db.record_sync_health(slug, "WB", kind, True, None, _now())
            except Exception as e:
                # Одна читаемая строка вместо полного стека: ошибки вроде
                # «у ключа нет нужной категории» — это настройка кабинета, а не
                # сбой кода, и трассировка по ним только прячет остальной лог.
                # Полный стек остаётся на debug-уровне, если понадобится копать.
                logger.error("WB %s / %s: %s", _store_label(slug), kind.upper(),
                             _error_message(e))
                logger.debug("Подробности ошибки WB %s/%s", slug, kind, exc_info=True)
                report[slug][kind] = {"ok": False, "error": _error_message(e)}
                db.record_sync_health(slug, "WB", kind, False, _error_message(e), _now())

    return report
