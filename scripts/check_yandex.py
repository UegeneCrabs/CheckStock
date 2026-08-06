"""
Разовый диагностический скрипт по Яндекс Маркету.

Смотрим, что реально отдаёт API, прежде чем писать синхронизацию — с Ozon
такой порядок трижды спас от неверных предположений по документации.

Отвечает на вопросы, от которых зависит структура кода:
  - какие магазины (campaignId) привязаны к ключу и по каким моделям работают;
  - какой у них кабинет (businessId) — из него берётся каталог;
  - сколько товаров в каталоге, у всех ли есть баркод;
  - совпадают ли артикулы Маркета с нашим каталогом WB;
  - какие типы остатков реально приходят и сколько в каждом;
  - как называются склады и сколько их.

Ничего не пишет в БД и никуда не отправляет — только печатает.

Запуск (из корня проекта, в .venv проекта):
    python scripts/check_yandex.py
    python scripts/check_yandex.py rimili
"""

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.stores import STORES
from app.yandex import api as ya_api
from app.yandex import tokens as ya_tokens

SHOW = 10


def _check_store(slug: str) -> None:
    api_key = ya_tokens.get_api_key(slug)
    print(f"\n=== {slug} ({STORES.get(slug, {}).get('name', slug.upper())}) ===")

    # 1. Магазины и кабинет
    try:
        raw_campaigns = ya_api.get_campaigns(api_key)
    except ya_api.YandexApiError as e:
        print(f"  магазины: ошибка — {e.friendly}")
        return

    campaigns = [ya_api.normalize_campaign(row) for row in raw_campaigns]
    print(f"  магазинов по ключу: {len(campaigns)}")
    for c in campaigns:
        print(f"    campaignId={c['campaign_id']}  businessId={c['business_id']}"
              f"  модель={c['placement']} -> {c['scheme']}  {c['domain']}")

    if not campaigns:
        print("    ключ не привязан ни к одному магазину")
        return

    business_ids = {c["business_id"] for c in campaigns if c["business_id"]}
    if len(business_ids) > 1:
        print(f"    ВНИМАНИЕ: кабинетов несколько ({business_ids}) — каталог придётся брать из каждого")

    # 2. Каталог
    business_id = ya_tokens.get_business_id(slug) or next(iter(business_ids), None)
    catalog = []
    if business_id:
        try:
            raw_catalog = ya_api.get_catalog(api_key, business_id)
            catalog = [ya_api.normalize_catalog_item(row) for row in raw_catalog]
        except ya_api.YandexApiError as e:
            print(f"  каталог: ошибка — {e.friendly}")

    if catalog:
        no_barcode = [p for p in catalog if not p["barcode"]]
        multi = [p for p in catalog if len(p["barcodes"]) > 1]
        archived = [p for p in catalog if p["archived"]]
        print(f"\n  каталог кабинета {business_id}: {len(catalog)} позиций")
        print(f"    без баркода: {len(no_barcode)} | с несколькими баркодами: {len(multi)}"
              f" | архивных: {len(archived)}")
        dup = [k for k, n in Counter(p["article"] for p in catalog).items() if n > 1]
        print(f"    дубли артикулов: {len(dup)} {dup[:5]}")
        print(f"    примеры (до {SHOW}):")
        for p in catalog[:SHOW]:
            print(f"      {p['article']:<18} bc={p['barcode']:<15} sku={p['market_sku']}"
                  f"  {p['name'][:34]}")

        # сверка с нашим каталогом WB
        ours = db.get_catalog_items(slug, "WB")
        if ours:
            our_articles = {i["article"] for i in ours}
            our_barcodes = {str(i["barcode"]) for i in ours if i["barcode"]}
            by_article = [p for p in catalog if p["article"] in our_articles]
            rest = [p for p in catalog if p["article"] not in our_articles]
            by_barcode = [p for p in rest if set(p["barcodes"]) & our_barcodes]
            print(f"\n    сверка с каталогом WB ({len(our_articles)} товаров):")
            print(f"      совпало по артикулу: {len(by_article)}")
            print(f"      совпало по баркоду:  {len(by_barcode)}")
            print(f"      не совпало никак:    {len(rest) - len(by_barcode)}")

    # 3. Остатки по каждому магазину
    for c in campaigns:
        cid = c["campaign_id"]
        print(f"\n  остатки campaignId={cid} ({c['scheme']}):")
        try:
            rows = ya_api.get_stocks(api_key, cid)
        except ya_api.YandexApiError as e:
            print(f"    ошибка — {e.friendly}")
            continue

        if not rows:
            print("    пусто")
            continue

        by_type: dict[str, int] = defaultdict(int)
        by_warehouse: dict[str, int] = defaultdict(int)
        for r in rows:
            for key, value in ya_api.stock_by_type(r["stocks"]).items():
                by_type[key] += value
            by_warehouse[str(r["warehouse_id"])] += ya_api.available_quantity(r["stocks"])

        print(f"    строк {len(rows)}, товаров {len({r['article'] for r in rows})},"
              f" складов {len(by_warehouse)}")
        print("    по типам остатка:", dict(sorted(by_type.items())))
        print("    по складам (доступно):", dict(sorted(by_warehouse.items(), key=lambda x: -x[1])))

        print(f"    пример строк (до {SHOW}):")
        for r in rows[:SHOW]:
            print(f"      {r['article']:<18} склад={r['warehouse_id']}"
                  f"  {ya_api.stock_by_type(r['stocks'])}")

    # 4. Склады Маркета
    try:
        warehouses = ya_api.get_fulfillment_warehouses(api_key)
        print(f"\n  склады Маркета (FBY): {len(warehouses)}")
        for w in warehouses[:SHOW]:
            print(f"    id={w.get('id')}  {w.get('name')}")
    except ya_api.YandexApiError as e:
        print(f"\n  склады Маркета: ошибка — {e.friendly}")


def main() -> None:
    wanted = [a.lower() for a in sys.argv[1:]]
    checked = False

    for slug in STORES:
        if wanted and slug.lower() not in wanted:
            continue
        if not ya_tokens.has_credentials(slug):
            continue
        checked = True
        _check_store(slug)

    if not checked:
        print("Не найдено магазинов с ключами Яндекс Маркета "
              f"({ya_tokens.SECRETS_PATH}). Проверять нечего.")


if __name__ == "__main__":
    main()
