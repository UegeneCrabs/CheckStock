"""Apply SKU-level cost of delivered goods from Yandex realisation XLSX reports.

Yandex may split one cabinet's monthly realisation across several files.  The
loader combines them by order, SKU and delivery date before updating the daily
financial P&L.  It deliberately changes only days represented in the files.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as etree
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from app.repositories import core, yandex_source_values

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
DELIVERED_SHEET = "Доставленные товары"
RETURNED_SHEET = "Возвращенные товары"


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = etree.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.iter(NS + "t")) for item in root.iter(NS + "si")]


def _cell_value(cell: etree.Element, strings: list[str]) -> str:
    inline = "".join(node.text or "" for node in cell.iter(NS + "t"))
    if inline:
        return inline.strip()
    value = cell.find(NS + "v")
    if value is None or value.text is None:
        return ""
    return strings[int(value.text)] if cell.get("t") == "s" else value.text.strip()


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference).group(0)
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - ord("A") + 1
    return index - 1


def _sheet_xml_name(archive: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = etree.fromstring(archive.read("xl/workbook.xml"))
    sheet = next(item for item in workbook.iter(NS + "sheet") if item.get("name") == sheet_name)
    relation_id = sheet.get(REL_NS + "id")
    rels = etree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    target = next(item.get("Target") for item in rels if item.get("Id") == relation_id)
    return "xl/" + target.lstrip("/")


def _rows(path: Path, sheet_name: str) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        strings = _shared_strings(archive)
        root = etree.fromstring(archive.read(_sheet_xml_name(archive, sheet_name)))
    result: list[list[str]] = []
    for row in root.iter(NS + "row"):
        cells = list(row.iter(NS + "c"))
        if not cells:
            continue
        values = [""] * (max(_column_index(cell.get("r")) for cell in cells) + 1)
        for cell in cells:
            values[_column_index(cell.get("r"))] = _cell_value(cell, strings)
        result.append(values)
    return result


def _iso_day(value: str) -> str:
    return datetime.strptime(value.strip(), "%d.%m.%Y").date().isoformat()


def _number(value: str) -> float:
    return float(value.replace(" ", "").replace(",", ".") or 0)


def _items(
    paths: list[Path], sheet_name: str, quantity_column: str, date_column: str
) -> dict[tuple[str, str, str], float]:
    """Return unique (order, SKU, event-day) quantities across reports."""

    result: dict[tuple[str, str, str], float] = {}
    for path in paths:
        header: dict[str, int] | None = None
        for row in _rows(path, sheet_name):
            if "Номер заказа" in row:
                header = {value: index for index, value in enumerate(row) if value}
                continue
            if header is None or not row or not row[0] or row[0] == "Итого:":
                continue
            try:
                order_id = row[header["Номер заказа"]].strip()
                article = row[header["Ваш SKU"]].strip()
                event_day = _iso_day(row[header[date_column]])
                quantity = _number(row[header[quantity_column]])
            except (IndexError, KeyError, ValueError):
                continue
            if order_id and article and quantity > 0:
                result[(order_id, article, event_day)] = quantity
    return result


def delivered_items(paths: list[Path]) -> dict[tuple[str, str, str], float]:
    items = _items(paths, DELIVERED_SHEET, "Доставлено, шт.", "Дата доставки товара")
    if not items:
        raise ValueError("В файлах не найдены строки листа «Доставленные товары»")
    return items


def returned_items(paths: list[Path]) -> dict[tuple[str, str, str], float]:
    return _items(
        paths,
        RETURNED_SHEET,
        "Возвращено, шт.",
        "Дата приёма возврата складом или сортировочным центром",
    )


def apply_costs(
    store_slug: str,
    items: dict[tuple[str, str, str], float],
    returns: dict[tuple[str, str, str], float],
) -> dict[str, object]:
    prices = {
        article: float(row["purchase_price"])
        for article, row in yandex_source_values.get_values(store_slug).items()
        if row.get("purchase_price") is not None
    }
    missing = sorted({article for _, article, _ in items | returns if article not in prices})
    if missing:
        raise ValueError("Нет себестоимости для SKU: " + ", ".join(missing[:10]))

    delivered_cost: defaultdict[str, float] = defaultdict(float)
    for (_, article, delivery_day), quantity in items.items():
        delivered_cost[delivery_day] += prices[article] * quantity
    returned_cost: defaultdict[str, float] = defaultdict(float)
    for (_, article, return_day), quantity in returns.items():
        returned_cost[return_day] += prices[article] * quantity

    now = datetime.now(UTC).isoformat(timespec="seconds")
    updated: list[str] = []
    with core.WRITE_LOCK, core.get_connection() as connection:
        for day in sorted(set(delivered_cost) | set(returned_cost)):
            cost = delivered_cost[day]
            row = connection.execute(
                "SELECT values_json FROM yandex_financial_daily_pnl WHERE store_slug=? AND report_date=?",
                (store_slug, day),
            ).fetchone()
            if row is None:
                continue
            values = json.loads(row["values_json"])
            returned_cost_value = round(
                returned_cost[day]
                if day in returned_cost
                else float(values.get("returned_cost_of_goods") or 0),
                2,
            )
            net_cost = round(cost - returned_cost_value, 2)
            values.update(
                {
                    "sold_cost_of_goods": net_cost,
                    "returned_cost_of_goods": returned_cost_value,
                    "cost_of_goods": net_cost,
                }
            )
            connection.execute(
                """
                UPDATE yandex_financial_daily_pnl
                   SET values_json=?, calculated_at=?
                 WHERE store_slug=? AND report_date=?
                """,
                (json.dumps(values), now, store_slug, day),
            )
            updated.append(day)
        connection.commit()
    return {
        "items": len(items),
        "quantity": round(sum(items.values()), 2),
        "returns": round(sum(returns.values()), 2),
        "days_updated": updated,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True)
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()
    paths = [Path(value) for value in args.files]
    report = apply_costs(args.store, delivered_items(paths), returned_items(paths))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
