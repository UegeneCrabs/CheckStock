import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.stores import STORES
from app.wb import api as wb_api
from app.wb import catalog as wb_catalog
from app.wb import tokens as wb_tokens


def preview(store_slug: str) -> None:
    label = STORES.get(store_slug, {}).get("name") or store_slug.upper()
    print(f"\n=== {label} ({store_slug})")

    try:
        cards = wb_api.get_cards_list(wb_tokens.get_token(store_slug))
    except Exception as e:
        print(f"  каталог не получен: {e}")
        return

    excluded_tag = wb_catalog.STALE_TAG if store_slug in wb_catalog.STALE_TAG_STORES else None
    items, stats = wb_catalog.build_items(cards, excluded_tag=excluded_tag)
    print(f"  карточек в кабинете: {stats['cards']}")
    print(f"  позиций после разбора: {len(items)}")
    if stats["multi_size"]:
        print(
            f"  карточек с несколькими размерами: {stats['multi_size']}"
            f" — каждая развернётся в отдельные строки"
        )
    if stats["no_article"]:
        print(f"  без артикула продавца (пропущены): {stats['no_article']}")
    if stats["no_barcode"]:
        print(f"  без баркода (пропущены): {stats['no_barcode']}")
    if stats["excluded_tag"]:
        print(f"  с тегом «{excluded_tag}» (исключены): {stats['excluded_tag']}")

    existing = {row["article"] for row in db.get_catalog_items(store_slug, "WB")}
    incoming = {item["article"] for item in items}

    print(f"\n  сейчас в базе: {len(existing)}")
    print(f"  совпадут:      {len(existing & incoming)}")
    print(f"  добавятся:     {len(incoming - existing)}")

    disappearing = sorted(existing - incoming)
    print(f"  исчезнут:      {len(disappearing)}")

    if disappearing:
        protected = db.articles_with_own_stock(store_slug, "WB")
        excluded_nm_ids = wb_catalog.tagged_nm_ids(cards, excluded_tag) if excluded_tag else set()
        forced = wb_catalog.articles_for_nm_ids(set(disappearing), excluded_nm_ids)
        with_stock = [a for a in disappearing if a in protected]
        kept_with_stock = [a for a in with_stock if a not in forced]

        print("\n  примеры исчезающих артикулов:")
        for article in disappearing[:15]:
            print(f"    {article}")
        if len(disappearing) > 15:
            print(f"    ... и ещё {len(disappearing) - 15}")

        if forced:
            print(f"\n  принудительно удалятся по тегу: {len(forced)}")
        if kept_with_stock:
            print(f"\n  из них с остатком на ФФ: {len(kept_with_stock)} — они сохранятся")

        share = len(disappearing) / len(existing) if existing else 0
        if share > 0.5:
            print("\n  ВНИМАНИЕ: исчезает больше половины каталога.")
            print("  Похоже, артикулы в базе и в кабинете WB заданы по-разному.")
            print("  Не запускайте с --apply, пока это не выяснено.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Предпросмотр каталога WB")
    parser.add_argument("--store", help="один магазин (slug), по умолчанию все")
    parser.add_argument("--apply", action="store_true", help="записать каталог в базу")
    args = parser.parse_args()

    slugs = [args.store] if args.store else list(STORES)
    slugs = [slug for slug in slugs if wb_tokens.has_token(slug)]

    if not slugs:
        print("Нет магазинов с токеном WB")
        return

    if args.apply:
        for slug in slugs:
            label = STORES.get(slug, {}).get("name") or slug.upper()
            try:
                print(f"{label}: {wb_catalog.sync_store(slug)}")
            except Exception as e:
                print(f"{label}: не выгружен — {e}")
        return

    for slug in slugs:
        preview(slug)

    print("\nЗапись не производилась. Для записи добавьте --apply")


if __name__ == "__main__":
    main()
