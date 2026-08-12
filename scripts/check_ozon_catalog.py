import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.ozon import api as ozon_api
from app.ozon import tokens as ozon_tokens
from app.stores import STORES

SHOW_LIMIT = 10


SERVICE_HINTS = ("инструкция", "пакет", "наклейка", "вкладыш")


def _looks_service(name: str, offer_id: str) -> bool:
    text = f"{name} {offer_id}".lower()
    return any(hint in text for hint in SERVICE_HINTS)


def _check_store(slug: str) -> None:
    client_id, api_key = ozon_tokens.get_credentials(slug)
    print(f"\n=== {slug} ({STORES[slug]['name']}) ===")

    try:
        listing = ozon_api.get_product_list(client_id, api_key)
    except ozon_api.OzonApiError as e:
        print(f"  список товаров: ошибка — {e.friendly}")
        return

    print(f"  карточек в кабинете: {len(listing)}")
    if not listing:
        return

    print(f"  поля списка: {sorted(listing[0])}")

    product_ids = [row["product_id"] for row in listing if row.get("product_id")]
    if not product_ids:
        product_ids = [row["id"] for row in listing if row.get("id")]

    try:
        raw = ozon_api.get_product_info(client_id, api_key, product_ids)
    except ozon_api.OzonApiError as e:
        print(f"  карточки: ошибка — {e.friendly}")
        return

    print(f"  получено карточек: {len(raw)}")
    if raw:
        print(f"  поля карточки: {sorted(raw[0])[:24]}")

    products = [ozon_api.normalize_product(row) for row in raw]

    no_sku = [p for p in products if not p["sku"]]
    no_bc = [p for p in products if not p["barcodes"]]
    multi_bc = [p for p in products if len(p["barcodes"]) > 1]
    archived = [p for p in products if p["archived"]]
    service = [p for p in products if _looks_service(p["name"], p["offer_id"])]

    print(
        f"\n  без SKU: {len(no_sku)} | без баркода: {len(no_bc)} | с несколькими баркодами: {len(multi_bc)}"
    )
    print(f"  архивных: {len(archived)} | похожих на служебные: {len(service)}")

    if multi_bc:
        print(f"    несколько баркодов (до {SHOW_LIMIT}):")
        for p in multi_bc[:SHOW_LIMIT]:
            print(f"      {p['offer_id']:<14} {p['barcodes']}  {p['name'][:34]}")

    if service:
        print(f"    служебные (до {SHOW_LIMIT}):")
        for p in service[:SHOW_LIMIT]:
            print(f"      {p['offer_id']:<14} {p['name'][:44]}")

    for field in ("offer_id", "sku"):
        values = [str(p[field]) for p in products if p[field]]
        dup = [k for k, c in Counter(values).items() if c > 1]
        print(f"  дубли {field}: {len(dup)} {dup[:5]}")

    catalog = db.get_catalog_items(slug)
    if not catalog:
        print("  наш каталог пуст — сверять не с чем")
        return

    our_articles = {item["article"] for item in catalog}
    our_barcodes = {str(item["barcode"]) for item in catalog if item["barcode"]}

    by_article = [p for p in products if p["offer_id"] in our_articles]
    rest = [p for p in products if p["offer_id"] not in our_articles]
    by_barcode = [p for p in rest if set(p["barcodes"]) & our_barcodes]
    nowhere = [p for p in rest if not (set(p["barcodes"]) & our_barcodes)]

    print(f"\n  сверка с нашим каталогом ({len(our_articles)} товаров):")
    print(f"    нашлось по артикулу: {len(by_article)}")
    print(f"    нашлось по баркоду:  {len(by_barcode)}")
    print(f"    не нашлось никак:    {len(nowhere)}")

    if by_barcode:
        print(f"    по баркоду (до {SHOW_LIMIT}) — это те, что сейчас теряются:")
        for p in by_barcode[:SHOW_LIMIT]:
            print(f"      ozon={p['offer_id']:<14} bc={p['barcodes']}  {p['name'][:30]}")

    if nowhere:
        print(f"    новые для нас (до {SHOW_LIMIT}):")
        for p in nowhere[:SHOW_LIMIT]:
            flag = " [служебное]" if _looks_service(p["name"], p["offer_id"]) else ""
            print(
                f"      ozon={p['offer_id']:<14} sku={p['sku']}"
                f"  bc={p['barcodes'][:1]}  {p['name'][:30]}{flag}"
            )


def main() -> None:
    wanted = [a.lower() for a in sys.argv[1:]]
    checked = False

    for slug in STORES:
        if wanted and slug.lower() not in wanted:
            continue
        if not ozon_tokens.has_credentials(slug):
            continue
        checked = True
        _check_store(slug)

    if not checked:
        print("Не найдено магазинов с доступами Ozon (secrets/ozon_tokens.json). Проверять нечего.")


if __name__ == "__main__":
    main()
