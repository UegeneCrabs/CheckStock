"""Проверить текущие цены WB по артикулам без записи в БД."""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.economics.wb.prices import _storefront_price_rows, calculate_wallet_price
from app.wb import api


def article_number(value: str) -> str:
    if not value.isascii() or not value.isdigit() or int(value) <= 0:
        raise argparse.ArgumentTypeError("Нужен числовой артикул WB")
    return str(int(value))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("articles", nargs="+", type=article_number, help="Артикулы WB через пробел")
    args = parser.parse_args(argv)
    print(f"Проверка WB: {datetime.now().astimezone():%d.%m.%Y %H:%M:%S %Z}")
    print("Реальный запрос к витрине. БД не изменяется; расчёт по старой СПП не используется.")
    report = api.get_storefront_products(args.articles)
    for message in report["errors"]:
        print("Ошибка: " + message)
    if not report["products"]:
        print("WB не вернул товары.")
        return 1
    try:
        discount = api.get_default_wallet_discount_percent()
    except api.WBApiError as error:
        print("Не загружена скидка WB Кошелька: " + error.friendly)
        discount = None
    if discount is not None:
        print(f"Скидка WB Кошелька: {discount:g}%")
    rows = _storefront_price_rows(report["products"])
    for row in rows:
        spp_price = row["customer_price_with_spp"]
        wallet = calculate_wallet_price(spp_price, discount, row["sale_conditions"])
        wallet_text = f"{wallet:.2f} ₽" if wallet is not None else "недоступна для этого товара"
        print(f"{row['article']}: с СПП — {spp_price:.2f} ₽; с WB Кошельком — {wallet_text}")
    priced = {row["nm_id"] for row in rows}
    missing = sorted(set(args.articles) - priced, key=int)
    if missing:
        print("Нет актуальной цены: " + ", ".join(missing))
    return int(bool(report["errors"] or report["failed_nm_ids"] or missing or discount is None))


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
