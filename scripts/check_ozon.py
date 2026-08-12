import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.ozon import api as ozon_api
from app.ozon import tokens as ozon_tokens
from app.stores import STORES

PREVIEW_ROWS = 8


def _print_warehouses(client_id: str, api_key: str) -> dict[str, bool]:

    try:
        warehouses = ozon_api.get_own_warehouses(client_id, api_key)
    except ozon_api.OzonApiError as e:
        print(f"  склады: ошибка — {e.friendly}")
        return {}

    if not warehouses:
        print("  складов продавца не найдено")
        return {}

    active = [w for w in warehouses if str(w.get("status")) not in ("disabled", "archived")]
    print(f"  склады продавца: всего {len(warehouses)}, активных {len(active)}")

    rfbs_by_name = {}
    for w in warehouses:
        name = w.get("name", "?")
        is_rfbs = bool(w.get("is_rfbs"))
        rfbs_by_name[name] = is_rfbs
        scheme = "rFBS" if is_rfbs else "FBS"

        if w in active:
            print(
                f"    [{scheme:4}] id={w.get('warehouse_id')}  {name!r}"
                f"  status={w.get('status')!r}  <- АКТИВЕН"
            )

    if not active:
        print("    все склады продавца выключены — схема FBS/rFBS сейчас не используется")
    return rfbs_by_name


def _print_fbo(client_id: str, api_key: str) -> None:
    try:
        rows = ozon_api.get_fbo_stock_by_warehouse(client_id, api_key)
    except ozon_api.OzonApiError as e:
        print(f"  остатки FBO: ошибка — {e.friendly}")
        return
    scheme, rfbs_by_name = "FBO", {}

    if not rows:
        print(f"  остатки {scheme}: пусто")
        return

    by_warehouse: dict[str, int] = defaultdict(int)
    for r in rows:
        by_warehouse[r.get("warehouse_name", "?")] += int(r.get("free_to_sell_amount") or 0)

    print(f"  остатки {scheme}: строк {len(rows)}, складов {len(by_warehouse)}")
    for name, total in sorted(by_warehouse.items(), key=lambda x: -x[1]):
        mark = ""
        if name in rfbs_by_name:
            mark = "  <- rFBS" if rfbs_by_name[name] else "  <- FBS"
        print(f"    {name!r}: {total}{mark}")

    print(f"    пример строк (до {PREVIEW_ROWS}):")
    for r in rows[:PREVIEW_ROWS]:
        print(
            f"      sku={r.get('sku')}  item_code={r.get('item_code')!r}"
            f"  склад={r.get('warehouse_name')!r}"
            f"  свободно={r.get('free_to_sell_amount')}"
            f"  резерв={r.get('reserved_amount')}"
            f"  в пути={r.get('promised_amount')}"
        )


def _check_catalog_match(store_slug: str, client_id: str, api_key: str) -> None:

    try:
        rows = ozon_api.get_fbo_stock_by_warehouse(client_id, api_key)
    except ozon_api.OzonApiError as e:
        print(f"  сверка с каталогом: ошибка — {e.friendly}")
        return

    catalog = db.get_catalog_items(store_slug)
    if not catalog:
        print("  сверка с каталогом: каталог этого магазина пуст")
        return

    articles = {item["article"] for item in catalog}
    codes = {str(r.get("item_code") or "").strip() for r in rows if r.get("item_code")}
    matched = codes & articles

    print(
        f"  сверка с каталогом: в каталоге {len(articles)}, "
        f"уникальных item_code {len(codes)}, совпало {len(matched)}"
    )

    if not matched and codes:
        print("    ВНИМАНИЕ: не совпало ничего — значит артикулы в Ozon")
        print("    отличаются от наших, матчить придётся по баркоду или SKU.")
        print("    Примеры item_code из Ozon:", sorted(codes)[:5])
        print("    Примеры артикулов у нас  :", sorted(articles)[:5])
    elif len(matched) < len(codes):
        missing = sorted(codes - articles)[:5]
        print(f"    нет в нашем каталоге ({len(codes - articles)}): {missing}")


def _print_analytics(client_id: str, api_key: str, skus: list[int]) -> None:

    if not skus:
        print("  аналитика остатков: нет SKU для запроса")
        return
    try:
        rows = ozon_api.get_stock_analytics(client_id, api_key, skus)
    except ozon_api.OzonApiError as e:
        print(f"  аналитика остатков: ошибка — {e.friendly}")
        return

    if not rows:
        print("  аналитика остатков: пусто")
        return

    normalized = [ozon_api.normalize_analytics_row(r) for r in rows]

    totals = defaultdict(int)
    clusters = defaultdict(int)
    for r in normalized:
        for key in ("available", "transit", "expiring", "excess", "defect", "returns"):
            totals[key] += r[key]
        if r["cluster_name"]:
            clusters[r["cluster_name"]] += r["available"]

    print(f"  аналитика остатков: строк {len(rows)}, кластеров {len(clusters)}")
    print(
        f"    доступно={totals['available']}  в пути={totals['transit']}"
        f"  излишки={totals['excess']}  истекает={totals['expiring']}"
        f"  брак={totals['defect']}  возвраты={totals['returns']}"
    )

    if clusters:
        print("    по кластерам (топ-8 по доступному):")
        for name, total in sorted(clusters.items(), key=lambda x: -x[1])[:8]:
            print(f"      {name!r}: {total}")


def _print_totals(client_id: str, api_key: str) -> list[int]:

    try:
        items = ozon_api.get_product_stocks(client_id, api_key)
    except ozon_api.OzonApiError as e:
        print(f"  тоталы по товарам: ошибка — {e.friendly}")
        return []

    by_type: dict[str, int] = defaultdict(int)
    skus: set[int] = set()
    for item in items:
        for stock in item.get("stocks") or []:
            by_type[str(stock.get("type") or "?")] += int(stock.get("present") or 0)
            if stock.get("sku"):
                skus.add(int(stock["sku"]))

    print(f"  тоталы по товарам: позиций {len(items)}, уникальных SKU {len(skus)}")
    for stock_type, total in sorted(by_type.items()):
        print(f"    type={stock_type!r}: {total}")
    return sorted(skus)


def main() -> None:
    any_store = False

    for slug in STORES:
        if not ozon_tokens.has_credentials(slug):
            continue

        any_store = True
        client_id, api_key = ozon_tokens.get_credentials(slug)
        print(f"\n=== {slug} ({STORES[slug]['name']}) ===")

        _print_warehouses(client_id, api_key)
        print()
        skus = _print_totals(client_id, api_key)
        print()
        _print_fbo(client_id, api_key)
        print()
        _print_analytics(client_id, api_key, skus)
        print()
        _check_catalog_match(slug, client_id, api_key)

    if not any_store:
        print("Ни для одного магазина нет доступов Ozon.")
        print(f"Добавь их в {ozon_tokens.TOKENS_PATH} по образцу")
        print(f"{ozon_tokens.TOKENS_PATH.with_name('ozon_tokens.example.json')}")


if __name__ == "__main__":
    main()
