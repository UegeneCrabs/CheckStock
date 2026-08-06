"""
Предпросмотр каталога WB перед первой записью в базу.

Зачем отдельный скрипт. Каталог WB до сих пор жил в базе сам по себе, и
никто не знает наверняка, совпадают ли артикулы в нём с артикулами продавца
из кабинета. Синхронизация сверяет списки: чего в кабинете нет — удаляет.
Если артикулы окажутся другими, первая же выгрузка заменит каталог целиком,
и вся история остатков повиснет на артикулах, которых больше нет в таблице.

Поэтому: сначала посмотреть, потом писать.

    python scripts/check_wb_catalog.py               # все магазины, без записи
    python scripts/check_wb_catalog.py --store tris  # один магазин
    python scripts/check_wb_catalog.py --apply       # записать в базу

Строки, по которым у нас есть собственный остаток (ФФ или мусорка), не
удаляются в любом случае — но увидеть их количество заранее полезно.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db                      # noqa: E402
from app.stores import STORES           # noqa: E402
from app.wb import api as wb_api        # noqa: E402
from app.wb import catalog as wb_catalog  # noqa: E402
from app.wb import tokens as wb_tokens  # noqa: E402


def preview(store_slug: str) -> None:
    label = STORES.get(store_slug, {}).get("name") or store_slug.upper()
    print(f"\n=== {label} ({store_slug})")

    try:
        cards = wb_api.get_cards_list(wb_tokens.get_token(store_slug))
    except Exception as e:
        print(f"  каталог не получен: {e}")
        return

    items, stats = wb_catalog.build_items(cards)
    print(f"  карточек в кабинете: {stats['cards']}")
    print(f"  позиций после разбора: {len(items)}")
    if stats["multi_size"]:
        print(f"  карточек с несколькими размерами: {stats['multi_size']}"
              f" — каждая развернётся в отдельные строки")
    if stats["no_article"]:
        print(f"  без артикула продавца (пропущены): {stats['no_article']}")
    if stats["no_barcode"]:
        print(f"  без баркода (пропущены): {stats['no_barcode']}")

    existing = {row["article"] for row in db.get_catalog_items(store_slug, "WB")}
    incoming = {item["article"] for item in items}

    print(f"\n  сейчас в базе: {len(existing)}")
    print(f"  совпадут:      {len(existing & incoming)}")
    print(f"  добавятся:     {len(incoming - existing)}")

    disappearing = sorted(existing - incoming)
    print(f"  исчезнут:      {len(disappearing)}")

    if disappearing:
        protected = db.articles_with_own_stock(store_slug, "WB")
        with_stock = [a for a in disappearing if a in protected]

        print("\n  примеры исчезающих артикулов:")
        for article in disappearing[:15]:
            print(f"    {article}")
        if len(disappearing) > 15:
            print(f"    ... и ещё {len(disappearing) - 15}")

        if with_stock:
            print(f"\n  из них с остатком на ФФ: {len(with_stock)} — они сохранятся")

        share = len(disappearing) / len(existing) if existing else 0
        if share > 0.5:
            print("\n  ВНИМАНИЕ: исчезает больше половины каталога.")
            print("  Похоже, артикулы в базе и в кабинете WB заданы по-разному.")
            print("  Не запускайте с --apply, пока это не выяснено.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Предпросмотр каталога WB")
    parser.add_argument("--store", help="один магазин (slug), по умолчанию все")
    parser.add_argument("--apply", action="store_true",
                        help="записать каталог в базу")
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
