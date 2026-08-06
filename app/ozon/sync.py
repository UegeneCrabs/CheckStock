"""
Синхронизация остатков Ozon по всем магазинам.

Что тянем:
1. Тоталы по товару в разрезе схем (fbo / fbs / rfbs) — /v4/product/info/stocks.
   Это единственный источник, который знает про все три схемы сразу.
2. Разрез FBO по складам Ozon — /v2/analytics/stock_on_warehouses.
Кластеры складов берём ТОЛЬКО из своей таблицы mp_warehouse_cluster и
никуда за ними не ходим. Раньше их тянул /v1/analytics/stocks, но этот метод
лимитирован жёстче всех прочих: три кабинета параллельно ловили 429 даже с
паузой в полторы секунды между запросами, а вперемешку с ними прилетали 500.
Платить лимитами и шумом в логах за название группы, к которой относится
склад, смысла нет — оно постоянное, одинаковое для всех магазинов и уже
сохранено. Новый склад просто окажется без ярлыка, на остатки это не влияет.

Каталог берём тот, что выгружен с самого Ozon (app/ozon/catalog.py), поэтому
сопоставление по item_code / offer_id тривиально: это один и тот же артикул
продавца. Матчить с каталогом WB бессмысленно — на TRIS 60 карточек Ozon не
совпадают с ним ни по артикулу, ни по баркоду.

Всё пишется с marketplace='OZON', поэтому остатки WB не затрагиваются:
ключ в mp_stock включает маркетплейс (см. app/db.py).
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from app import db
from app.ozon import api as ozon_api
from app.ozon import tokens as ozon_tokens
from app.stores import STORES

logger = logging.getLogger("checkstock.ozon_sync")

MARKETPLACE = "OZON"
_DB_LOCK = db.WRITE_LOCK

# Схемы, которые может вернуть /v4/product/info/stocks.
SCHEMES = ("fbo", "fbs", "rfbs")

# Сколько SKU спрашиваем за раз ради кластеров и сколько таких заходов
# делаем максимум. Раньше метод вызывался по ВСЕМ SKU — это давало десятки
# запросов подряд и стабильный 429, хотя нужны от него лишь названия
# кластеров для полутора десятков складов.
def _store_label(store_slug: str) -> str:
    """Имя магазина для логов. В кабинете Ozon это отдельное юрлицо,
    и в отчёте важно видеть, у кого именно не выгрузилось."""
    store = STORES.get(store_slug) or {}
    return store.get("name") or store_slug.upper()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_message(e: Exception) -> str:
    if isinstance(e, ozon_api.OzonApiError):
        return e.friendly
    return f"непредвиденная ошибка ({type(e).__name__}): {e}"


def _totals_by_scheme(items: list[dict], known_articles: set[str]) -> dict[str, dict[str, int]]:
    """{схема: {артикул: количество}} из ответа /v4/product/info/stocks.

    Товары, которых нет в нашем каталоге, пропускаем: у магазина на Ozon
    обычно продаётся лишь часть ассортимента (у RIMILI 77 из 540).
    """
    totals: dict[str, dict[str, int]] = {scheme: {} for scheme in SCHEMES}

    for item in items:
        article = str(item.get("offer_id") or "").strip()
        if not article or article not in known_articles:
            continue

        for stock in item.get("stocks") or []:
            scheme = str(stock.get("type") or "").lower()
            if scheme not in totals:
                continue
            try:
                present = int(stock.get("present") or 0)
            except (TypeError, ValueError):
                continue
            totals[scheme][article] = totals[scheme].get(article, 0) + present

    return totals


def _cluster_by_warehouse() -> dict[str, str]:
    """{склад: кластер} из своей таблицы. К API не обращаемся — см. модульный
    комментарий. Если склада в таблице нет, он просто останется без ярлыка."""
    return db.get_warehouse_clusters(MARKETPLACE)


def sync_store(store_slug: str) -> int:
    """Полная синхронизация одного магазина. Возвращает число товаров,
    по которым нашлись остатки."""
    ozon_api.set_store_context(_store_label(store_slug))
    client_id, api_key = ozon_tokens.get_credentials(store_slug)

    # каталог Ozon, а не наш: артикулы и баркоды у площадок свои
    catalog = db.get_catalog_items(store_slug, MARKETPLACE)
    if not catalog:
        return 0
    known_articles = {item["article"] for item in catalog}

    # 1. Тоталы по всем трём схемам
    items = ozon_api.get_product_stocks(client_id, api_key)
    totals = _totals_by_scheme(items, known_articles)

    # 2. Разрез FBO по складам
    fbo_rows = ozon_api.get_fbo_stock_by_warehouse(client_id, api_key)

    # 3. Кластеры — из своей таблицы, без запросов к Ozon
    clusters = _cluster_by_warehouse()

    now = _now()
    warehouse_entries: list[tuple[str, str, str | None, int, str]] = []
    seen_articles: set[str] = set()

    for row in fbo_rows:
        article = str(row.get("item_code") or "").strip()
        if not article or article not in known_articles:
            continue
        warehouse = str(row.get("warehouse_name") or "").strip()
        if not warehouse:
            continue
        try:
            quantity = int(row.get("free_to_sell_amount") or 0)
        except (TypeError, ValueError):
            continue

        seen_articles.add(article)
        warehouse_entries.append((article, warehouse, clusters.get(warehouse), quantity, now))

    # Пишем всё под локом: сеть уже отработала, остались короткие запросы к SQLite.
    # Маркетплейс указан явно — данные WB в тех же таблицах не затрагиваются.
    with _DB_LOCK:
        db.replace_mp_warehouse_stock(store_slug, MARKETPLACE, "fbo", warehouse_entries)

        for scheme in SCHEMES:
            scheme_totals = totals[scheme]
            # проходим по всему каталогу, чтобы обнулившиеся позиции удалились
            for item in catalog:
                db.upsert_mp_stock(
                    store_slug, item["article"], MARKETPLACE, scheme,
                    scheme_totals.get(item["article"], 0), now,
                )

    covered = len(seen_articles | {a for s in totals.values() for a in s})
    logger.info(
        "Ozon %s: товаров с остатками %s, строк по складам %s, кластеров %s",
        _store_label(store_slug), covered, len(warehouse_entries), len(set(clusters.values())),
    )
    ozon_api.clear_store_context()
    return covered


def sync_all() -> dict:
    """Синхронизирует все магазины, у которых есть доступы Ozon.

    Магазины идут параллельно — это независимые запросы к разным кабинетам.
    Внутри магазина запросы последовательны: у аналитических методов Ozon
    жёсткие лимиты, и параллелить их внутри одного кабинета значит ловить 429.

    Формат отчёта тот же, что у WB, чтобы страница синхронизации умела
    показывать оба маркетплейса одинаково.
    """
    report: dict = {}
    active_slugs = []

    for slug in STORES:
        if ozon_tokens.has_credentials(slug):
            report[slug] = {"token": True, "ozon": None}
            active_slugs.append(slug)
        else:
            report[slug] = {"token": False, "ozon": None}

    if not active_slugs:
        return report

    with ThreadPoolExecutor(max_workers=max(1, len(active_slugs))) as executor:
        future_to_slug = {executor.submit(sync_store, slug): slug for slug in active_slugs}
        for future in as_completed(future_to_slug):
            slug = future_to_slug[future]
            try:
                report[slug]["ozon"] = {"ok": True, "count": future.result()}
                db.record_sync_health(slug, MARKETPLACE, "stocks", True, None, _now())
            except Exception as e:
                logger.error(
                    "Ozon %s: остатки не выгружены — %s",
                    _store_label(slug), _error_message(e),
                )
                logger.debug("Подробности ошибки Ozon %s", slug, exc_info=True)
                report[slug]["ozon"] = {"ok": False, "error": _error_message(e)}
                db.record_sync_health(slug, MARKETPLACE, "stocks", False, _error_message(e), _now())

    return report
