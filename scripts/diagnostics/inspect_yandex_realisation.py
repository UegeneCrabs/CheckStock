"""Print summary rows from Yandex Market's XLSX realisation report.

The report uses a stale worksheet dimension, so ordinary spreadsheet readers
can expose only its first row.  This diagnostic reads the worksheet XML
directly and is useful when reconciling historical sales with API data.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as etree
import zipfile
from pathlib import Path

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = etree.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.iter(NS + "t")) for item in root.iter(NS + "si")]


def _cell_value(cell: etree.Element, strings: list[str]) -> str:
    inline = "".join(node.text or "" for node in cell.iter(NS + "t"))
    if inline:
        return inline
    value = cell.find(NS + "v")
    if value is None or value.text is None:
        return ""
    if cell.get("t") == "s":
        return strings[int(value.text)]
    return value.text


def summary_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        strings = _shared_strings(archive)
        root = etree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    result = []
    for row in root.iter(NS + "row"):
        values = [_cell_value(cell, strings) for cell in row.iter(NS + "c")]
        if any(values):
            result.append(values)
    return result


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    for raw_path in sys.argv[1:]:
        path = Path(raw_path)
        print(f"\n{path.name}")
        for row in summary_rows(path):
            print(" | ".join(row))


if __name__ == "__main__":
    main()
