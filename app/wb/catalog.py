"""
Выгрузка каталога товаров с Wildberries.

До этого каталог WB был единственным, который система ниоткуда не брала: он
просто лежал в stock_items с давних времён. Из-за этого товар, заведённый в
кабинете WB, в системе не появлялся — остаток по нему приходил, но синхронизация
перебирает каталог из базы и всё, чего там нет, молча пропускает.

Забирается методом /content/v2/get/cards/list (категория токена «Контент»).

Раскладка полей у WB своя, не как у Ozon и Яндекса:

- артикул   — nmID, то есть артикул WB. Именно он у Wildberries считается
              артикулом товара и печатается в отчётах;
- баркод    — skus из sizes: штрихкод хранится у размера, а не у карточки;
- название  — vendorCode, у него подчёркивания заменяются пробелами. Продавцы
              пишут «Гирлянда_ретро_20м», и в таблице это читается плохо;
- mp_sku и mp_product_id не заполняются: у WB нет второго идентификатора,
  который был бы нам нужен, а nmID уже лежит в артикуле;
- mp_updated_at — updatedAt карточки: когда её последний раз меняли в
  кабинете. Это про площадку, а не про нашу базу.

Про размеры. У карточки может быть несколько размеров, и у каждого свой
штрихкод — на складе это разные физические товары с разными остатками. nmID
при этом один на всю карточку, а у нас артикул это ключ позиции. Поэтому
карточка с размерной сеткой разворачивается в строки «249801234 / 42»,
«249801234 / 44». Схлопнуть их в одну значило бы показать остаток одного
размера, а остальные потерять.

Каталог WB не связывается с каталогами Ozon и Яндекса — там свои артикулы и
свои баркоды, marketplace входит в ключ каждой таблицы.
"""

import logging
from datetime import datetime, timezone

from app import db
from app.stores import STORES
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens

logger = logging.getLogger("checkstock.wb_catalog")

MARKETPLACE = "WB"


def _store_label(store_slug: str) -> str:
    store = STORES.get(store_slug) or {}
    return store.get("name") or store_slug.upper()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_message(e: Exception) -> str:
    if isinstance(e, wb_api.WBApiError):
        return e.friendly
    return f"непредвиденная ошибка ({type(e).__name__}): {e}"


def clean_name(vendor_code: str) -> str:
    """Название из артикула продавца: подчёркивания -> пробелы.

    Заменяем, а не вырезаем: «Гирлянда_ретро_20м» без замены превратилось бы
    в «Гирляндаретро20м». Лишние пробелы схлопываем — встречается и «__».
    """
    return " ".join((vendor_code or "").replace("_", " ").split())


def build_items(cards: list[dict]) -> tuple[list[dict], dict]:
    """Карточки WB -> строки каталога. Возвращает (позиции, счётчики).

    Вынесено отдельно от сети, чтобы разбор можно было проверить на выгрузке
    без обращения к WB — этим пользуется scripts/check_wb_catalog.py.
    """
    items: list[dict] = []
    stats = {"cards": len(cards), "no_article": 0, "no_barcode": 0, "multi_size": 0}

    for card in cards:
        product = wb_api.normalize_card(card)
        nm_id = product["nm_id"]

        if not nm_id:
            # Без nmID карточку не с чем связать: это и есть артикул товара.
            stats["no_article"] += 1
            continue

        sizes = product["sizes"]
        if not sizes:
            stats["no_barcode"] += 1
            continue

        if len(sizes) > 1:
            stats["multi_size"] += 1

        name = clean_name(product["vendor_code"]) or product["title"]

        for size in sizes:
            suffix = f" / {size['tech_size']}" if len(sizes) > 1 and size["tech_size"] else ""
            items.append({
                "article": f"{nm_id}{suffix}",
                "barcode": size["barcode"],
                "name": name,
                "mp_sku": None,
                "mp_product_id": None,
                "mp_updated_at": product["updated_at"],
                "is_service": False,
            })

    return items, stats


def sync_store(store_slug: str, apply: bool = True) -> dict:
    """Обновляет каталог WB одного магазина. Возвращает отчёт для UI.

    apply=False считает то же самое, но в базу не пишет — режим предпросмотра
    для скрипта проверки. Первая выгрузка сверяется с тем, что накопилось за
    всё время, и посмотреть на её итог до записи стоит.
    """
    token = wb_tokens.get_token(store_slug)

    cards = wb_api.get_cards_list(token)
    items, stats = build_items(cards)

    if not items:
        # Пустой ответ не повод стирать каталог: скорее всего это сбой доступа,
        # а не кабинет, из которого убрали все карточки.
        logger.warning("Каталог WB %s: карточек не пришло, каталог не трогаем",
                       _store_label(store_slug))
        return {"total": 0, "added": 0, "updated": 0, "removed": 0, "kept": 0, **stats}

    if not apply:
        return {"total": len(items), "dry_run": True, **stats}

    with db.WRITE_LOCK:
        result = db.replace_catalog(store_slug, MARKETPLACE, items, _now())

    report = {"total": len(items), **stats, **result}
    logger.info("Каталог WB %s: %s", _store_label(store_slug), report)
    return report


def sync_all() -> dict:
    """Обновляет каталоги WB по всем магазинам с токеном.

    Последовательно: у контентного API WB лимит считается на кабинет, а
    выгрузка каталога идёт заметно реже, чем остатки, — гнаться не за чем.
    """
    report: dict = {}

    for slug in STORES:
        if not wb_tokens.has_token(slug):
            continue
        try:
            report[slug] = {"ok": True, **sync_store(slug)}
            db.record_sync_health(slug, MARKETPLACE, "catalog", True, None, _now())
        except Exception as e:
            message = _error_message(e)
            logger.error("WB %s: каталог не выгружен — %s", _store_label(slug), message)
            logger.debug("Подробности ошибки каталога WB %s", slug, exc_info=True)
            report[slug] = {"ok": False, "error": message}
            db.record_sync_health(slug, MARKETPLACE, "catalog", False, message, _now())

    return report
