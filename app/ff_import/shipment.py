"""
Отгрузка стока с фулфилмента.

По механике это половина перемещения: тот же разбор таблицы, тот же поиск
товара по каталогу и та же проверка остатка — только получателя нет, товар
уходит со склада наружу. Поэтому разбор строк и резолв позиций берём прямо
из transfer.py, а своё здесь только списание и примечание к отгрузке.

Правила, о которых договорились:

- не хватает остатка хоть по одной позиции — не проводится ничего. Если
  машина уже уехала, значит товар взяли откуда-то ещё, и сначала нужно
  привести остатки в порядок, а потом отгружать;
- один товар дважды в одной отгрузке запрещён — почти всегда это случайно
  продублированная строка, а тихое суммирование прятало бы ошибку;
- повторно провести тот же файл или ту же ссылку нельзя (см. db.used_sources).

Отдельный случай — мусорка. Это когда товар у нас числится, а фулфилмент
говорит, что его нет. Формально это тоже списание со склада, но исчезать
бесследно он не должен: количество перекладывается в trash_stock вместе со
складом, на котором потерялось, чтобы потом можно было разобраться.
"""

from datetime import datetime, timezone

from app import db
from app.ff_import.importer import FFImportError
from app.ff_import.transfer import (
    _parse_transfer_rows,
    entries_from_sheet,
    entries_from_xlsx,
    resolve_entries,
)

__all__ = [
    "FFImportError",
    "entries_from_sheet",
    "entries_from_xlsx",
    "ship",
    "validate_route",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_route(fulfillment: str, marketplace: str) -> None:
    """Проверяет, откуда уходит товар, ещё до разбора позиций."""
    if not fulfillment:
        raise FFImportError("выберите фулфилмент, с которого уходит товар")
    if not marketplace:
        raise FFImportError("выберите маркетплейс")
    if marketplace not in db.MARKETPLACES:
        raise FFImportError("неизвестный маркетплейс")


def split_by_sign(entries: list[tuple[str, int, str, str]]) -> tuple[list, list]:
    """Делит позиции на недостачу и излишек.

    Минус допустим только вместе с галочкой «в мусорку» и означает обратное:
    фулфилмент отдал БОЛЬШЕ, чем у нас числилось. Механика та же, только знак
    другой — остаток растёт, а запись в мусорке уменьшается. Отдельная кнопка
    под это не нужна: знак читается однозначно.
    """
    write_off, surplus = [], []
    for article, quantity, name, barcode in entries:
        if quantity < 0:
            surplus.append((article, -quantity, name, barcode))
        else:
            write_off.append((article, quantity, name, barcode))
    return write_off, surplus


def check_availability(
    store_slug: str,
    entries: list[tuple[str, int, str, str]],
    fulfillment: str,
    marketplace: str,
) -> None:
    """Хватает ли остатка по каждой позиции.

    Перечисляем сразу все нехватки: исправлять их по одной, каждый раз
    заново загружая файл, — то ещё удовольствие.
    """
    shortages = []
    for article, quantity, name, _barcode in entries:
        available = db.get_ff_stock_one(store_slug, article, fulfillment, marketplace)
        if quantity > available:
            shortages.append(f"{article} ({name}): отгружают {quantity}, есть {available}")

    if shortages:
        raise FFImportError(
            "не хватает остатка на фулфилменте — "
            + "; ".join(shortages)
            + ". Отгрузка отменена целиком."
        )


def ship(
    store_slug: str,
    raw_entries: list[dict],
    fulfillment: str,
    marketplace: str,
    to_trash: bool = False,
) -> list[dict]:
    """Полный цикл: проверка направления -> разбор позиций -> проверка
    остатков -> списание одной транзакцией. Возвращает списанные позиции.

    to_trash — товар не отгружен, а потерян: уходит в мусорку, а не в никуда.
    Вместе с этой галочкой количество может быть отрицательным — это излишек:
    фулфилмент отдал больше, чем у нас числилось. Остаток тогда прибавляется,
    а в мусорке появляется отрицательная запись.

    Проверка и запись идут под общим локом: между «проверил, что хватает» и
    «списал» не должна вклиниться другая операция по тем же ячейкам.
    """
    validate_route(fulfillment, marketplace)

    # каталог берём по маркетплейсу отгрузки: артикулы и баркоды у площадок
    # свои, каталог Ozon с каталогом WB не пересекается
    entries = resolve_entries(store_slug, raw_entries, marketplace,
                              allow_negative=to_trash)

    write_off, surplus = split_by_sign(entries)

    if surplus and not to_trash:
        raise FFImportError(
            "отрицательное количество допустимо только с галочкой «в мусорку» — "
            "так отмечают излишек, когда фулфилмент отдал больше, чем числилось"
        )

    now = _now()

    with db.WRITE_LOCK:
        if write_off:
            check_availability(store_slug, write_off, fulfillment, marketplace)

        try:
            if surplus:
                db.apply_ff_surplus(
                    store_slug,
                    [(article, quantity) for article, quantity, _n, _b in surplus],
                    fulfillment, marketplace, now,
                )
            if write_off:
                apply = db.apply_ff_trash if to_trash else db.apply_ff_shipment
                apply(store_slug,
                      [(article, quantity) for article, quantity, _n, _b in write_off],
                      fulfillment, marketplace, now)
        except ValueError as e:
            # понятный текст вместо технической ошибки транзакции
            raise FFImportError(str(e)) from e

    return [
        {"article": a, "name": n, "barcode": b, "quantity": q}
        for a, q, n, b in entries
    ]


def entries_from_rows(rows: list[list[str]]) -> list[dict]:
    """Позиции отгрузки из уже прочитанной таблицы — используется тестами
    и диагностикой, чтобы не гонять файл через диск."""
    return _parse_transfer_rows(rows)
