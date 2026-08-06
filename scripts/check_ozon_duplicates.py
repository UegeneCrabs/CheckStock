"""
Разовая проверка: есть ли на Ozon несколько карточек (offer_id) с одним SKU.

Зачем. В выгрузке сопоставления по TRIS у 54 товаров из 58 старая и новая
карточка имеют одинаковый SKU. Остаток на Ozon считается по SKU, а не по
карточке, поэтому если это правда — две строки в интерфейсе покажут один и
тот же остаток дважды. Прежде чем закладывать это в логику, надо увидеть,
что реально отдаёт API.

Скрипт отвечает на три вопроса:
  1. Приходит ли один SKU под несколькими offer_id.
  2. Одинаковые ли у них остатки (если да — сток общий, складывать нельзя).
  3. Сколько всего товаров потеряется, если матчить строго по артикулу.

Никуда ничего не пишет — только печатает.

Запуск (из корня проекта, в .venv проекта):
    python scripts/check_ozon_duplicates.py
    python scripts/check_ozon_duplicates.py tris
"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.ozon import api as ozon_api
from app.ozon import tokens as ozon_tokens
from app.stores import STORES

SHOW_LIMIT = 15


def _stock_rows(items: list[dict]) -> list[dict]:
    """Разворачиваем ответ /v4/product/info/stocks в плоские строки."""
    rows = []
    for item in items:
        offer_id = str(item.get("offer_id") or "").strip()
        for stock in item.get("stocks") or []:
            sku = stock.get("sku")
            if not sku:
                continue
            rows.append({
                "offer_id": offer_id,
                "sku": int(sku),
                "scheme": str(stock.get("type") or "?").lower(),
                "present": int(stock.get("present") or 0),
                "reserved": int(stock.get("reserved") or 0),
            })
    return rows


def _report_shared_skus(rows: list[dict]) -> None:
    """Главный вопрос: один SKU под несколькими артикулами продавца."""
    by_sku: dict[int, dict[str, dict]] = defaultdict(dict)
    for r in rows:
        # ключ — SKU + схема: у одного SKU остатки FBO и FBS считаются отдельно
        by_sku[r["sku"]].setdefault(r["scheme"], {})[r["offer_id"]] = r

    shared = {
        sku: schemes for sku, schemes in by_sku.items()
        if any(len(offers) > 1 for offers in schemes.values())
    }

    print(f"\n  SKU всего: {len(by_sku)} | под несколькими offer_id: {len(shared)}")

    if not shared:
        print("    Дублей нет: каждый SKU приходит ровно под одним артикулом продавца.")
        print("    Значит карточки в выгрузке действительно разные позиции,")
        print("    и отдельные строки с отдельными остатками корректны.")
        return

    equal_stock = 0
    differing = 0

    for sku, schemes in shared.items():
        for scheme, offers in schemes.items():
            if len(offers) < 2:
                continue
            values = {r["present"] for r in offers.values()}
            if len(values) == 1:
                equal_stock += 1
            else:
                differing += 1

    print(f"    из них остаток одинаковый у всех карточек: {equal_stock}")
    print(f"    из них остаток различается:                {differing}")

    if equal_stock and not differing:
        print("\n    ВЫВОД: сток у карточек с общим SKU совпадает всегда.")
        print("    Это один физический остаток, показанный дважды. Складывать")
        print("    или показывать двумя строками нельзя — получится двойной остаток.")
    elif differing:
        print("\n    ВЫВОД: остатки различаются — карточки ведут свой учёт,")
        print("    отдельные строки корректны.")

    print(f"\n    примеры (до {SHOW_LIMIT}):")
    shown = 0
    for sku, schemes in shared.items():
        for scheme, offers in schemes.items():
            if len(offers) < 2 or shown >= SHOW_LIMIT:
                continue
            shown += 1
            parts = "  ".join(
                f"{offer_id}={r['present']}" for offer_id, r in sorted(offers.items())
            )
            print(f"      sku={sku}  {scheme}:  {parts}")


def _report_catalog_match(store_slug: str, rows: list[dict]) -> None:
    """Сколько товаров потеряется, если матчить строго по артикулу."""
    catalog = db.get_catalog_items(store_slug)
    if not catalog:
        print("\n  каталог пуст — сверять не с чем")
        return

    articles = {item["article"] for item in catalog}
    offers = {r["offer_id"] for r in rows if r["offer_id"]}
    with_stock = {r["offer_id"] for r in rows if r["present"] > 0 and r["offer_id"]}

    matched = offers & articles
    unmatched = sorted(offers - articles)
    lost = sorted(with_stock - articles)

    print(f"\n  артикулов на Ozon: {len(offers)} | совпало с каталогом: {len(matched)}")
    print(f"  не сопоставлено: {len(unmatched)}")
    if unmatched:
        print(f"    примеры: {unmatched[:SHOW_LIMIT]}")
    print(f"  из них с ненулевым остатком (реально теряем): {len(lost)}")
    if lost:
        print(f"    {lost[:SHOW_LIMIT]}")


def main() -> None:
    wanted = [a.lower() for a in sys.argv[1:]]
    checked = False

    for slug in STORES:
        if wanted and slug.lower() not in wanted:
            continue
        if not ozon_tokens.has_credentials(slug):
            continue

        checked = True
        client_id, api_key = ozon_tokens.get_credentials(slug)
        print(f"\n=== {slug} ({STORES[slug]['name']}) ===")

        try:
            items = ozon_api.get_product_stocks(client_id, api_key)
        except ozon_api.OzonApiError as e:
            print(f"  ошибка: {e.friendly}")
            continue

        rows = _stock_rows(items)
        print(f"  позиций в ответе: {len(items)} | строк остатков: {len(rows)}")

        _report_shared_skus(rows)
        _report_catalog_match(slug, rows)

    if not checked:
        print("Не найдено магазинов с доступами Ozon "
              "(secrets/ozon_tokens.json). Проверять нечего.")


if __name__ == "__main__":
    main()
