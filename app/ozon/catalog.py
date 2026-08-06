"""
Выгрузка каталога товаров с Ozon.

Зачем отдельно от остатков. Ассортимент на площадках не совпадает: у TRIS
в кабинете Ozon 232 карточки против 433 позиций каталога WB, причём у части
товаров на Ozon другой артикул и другой баркод — новые карточки идут с
собственной серией баркодов (2075000000xxx вместо наших 2041904540146).
Связать их с нашими товарами автоматически нельзя: ни артикул, ни баркод не
совпадают, проверено — ноль совпадений по баркоду из 68 карточек.

Поэтому каталог Ozon живёт своей жизнью: карточка сама себе товар, со своим
артикулом, баркодом и остатком. Это же даёт таблице работать целиком, а не
терять строки из-за несопоставленных позиций.

Забирается в два шага: /v3/product/list отдаёт идентификаторы, а
/v3/product/info/list — подробности пачками по 1000.
"""

import logging
from datetime import datetime, timezone

from app import db
from app.ozon import api as ozon_api
from app.ozon import tokens as ozon_tokens
from app.stores import STORES

logger = logging.getLogger("checkstock.ozon_catalog")

MARKETPLACE = "OZON"

# Служебные позиции: в каталоге Ozon рядом с товарами лежат «Инструкция мойка 1»
# и подобные — они нужны площадке, но товаром не являются и остатки по ним
# смысла не имеют.
#
# Ищем по артикулу, а НЕ по названию: у карточки «Инструкция мойка 4» название
# как раз нормальное — «Мойка высокого давления аккумуляторная». Фильтр по
# названию спрятал бы вместе с ней настоящие мойки.
SERVICE_HINTS = ("инструкция", "вкладыш", "наклейка", "листовка")

# Ozon иногда подставляет собственный технический код вместо баркода.
# В поиск по баркоду такой пускать нельзя — на коробке его нет.
INTERNAL_BARCODE_PREFIX = "OZN"


def _store_label(store_slug: str) -> str:
    store = STORES.get(store_slug) or {}
    return store.get("name") or store_slug.upper()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_service(offer_id: str) -> bool:
    text = offer_id.casefold()
    return any(hint in text for hint in SERVICE_HINTS)


def _pick_barcode(barcodes: list[str]) -> str:
    """Из нескольких баркодов берём первый пригодный для поиска.

    У карточек встречается пара вида ['2037632491300', 'OZN2484175536'] —
    второй это внутренний код Ozon, а не то, что напечатано на коробке.
    """
    for barcode in barcodes:
        if not barcode.upper().startswith(INTERNAL_BARCODE_PREFIX):
            return barcode
    return ""


def sync_store(store_slug: str) -> dict:
    """Обновляет каталог Ozon одного магазина. Возвращает отчёт для UI."""
    ozon_api.set_store_context(_store_label(store_slug))
    client_id, api_key = ozon_tokens.get_credentials(store_slug)

    listing = ozon_api.get_product_list(client_id, api_key)
    if not listing:
        return {"total": 0, "added": 0, "updated": 0, "removed": 0,
                "service": 0, "no_barcode": 0}

    product_ids = [
        row.get("product_id") or row.get("id")
        for row in listing
        if row.get("product_id") or row.get("id")
    ]
    raw = ozon_api.get_product_info(client_id, api_key, product_ids)

    items = []
    service = 0
    no_barcode = 0

    for row in raw:
        product = ozon_api.normalize_product(row)
        article = product["offer_id"]
        if not article:
            continue  # без артикула продавца карточку не с чем связать

        is_service = _is_service(article)
        service += int(is_service)

        barcode = _pick_barcode(product["barcodes"])
        if not barcode and not is_service:
            no_barcode += 1

        items.append({
            "article": article,
            "barcode": barcode,
            "name": product["name"],
            "mp_sku": product["sku"],
            "mp_product_id": product["product_id"],
            "mp_updated_at": product["updated_at"],
            "is_service": is_service,
        })

    with db.WRITE_LOCK:
        result = db.replace_catalog(store_slug, MARKETPLACE, items, _now())

    report = {
        "total": len(items),
        "service": service,
        "no_barcode": no_barcode,
        **result,
    }
    logger.info("Каталог Ozon %s: %s", _store_label(store_slug), report)
    ozon_api.clear_store_context()
    return report


def sync_all() -> dict:
    """Обновляет каталоги Ozon по всем магазинам с доступами.

    Последовательно, а не параллельно: каталог тянется перед остатками, и
    класть на лимиты Ozon сразу все кабинеты нет смысла — выгрузка каталога
    идёт заметно реже, чем синхронизация остатков.
    """
    report: dict = {}

    for slug in STORES:
        if not ozon_tokens.has_credentials(slug):
            continue
        try:
            report[slug] = {"ok": True, **sync_store(slug)}
            db.record_sync_health(slug, MARKETPLACE, "catalog", True, None, _now())
        except ozon_api.OzonApiError as e:
            logger.error("Ozon %s: каталог не выгружен — %s", _store_label(slug), e.friendly)
            report[slug] = {"ok": False, "error": e.friendly}
            db.record_sync_health(slug, MARKETPLACE, "catalog", False, e.friendly, _now())
        except Exception as e:
            logger.error("Ozon %s: каталог не выгружен — %s: %s",
                         _store_label(slug), type(e).__name__, e)
            logger.debug("Подробности ошибки каталога Ozon %s", slug, exc_info=True)
            report[slug] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            db.record_sync_health(slug, MARKETPLACE, "catalog", False,
                                  f"{type(e).__name__}: {e}", _now())

    return report
