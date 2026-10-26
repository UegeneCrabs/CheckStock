from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

from app.core.constants import OZON_STORAGE, OzonStorageState
from app.dto.ozon_storage import (
    StorageDay,
    StorageEvidence,
    StoragePeriod,
    StorageReport,
    StorageRow,
    StorageSnapshot,
    StorageStoreSnapshot,
)


def placement_report(content: bytes, period: StoragePeriod) -> StorageReport:
    if len(content) > OZON_STORAGE.max_download_bytes:
        raise ValueError("Storage report download limit exceeded")
    ns = OZON_STORAGE.xml_namespace
    rows: list[StorageRow] = []
    required = (
        OZON_STORAGE.date_header,
        OZON_STORAGE.sku_header,
        OZON_STORAGE.article_header,
        OZON_STORAGE.cost_header,
    )
    with ZipFile(BytesIO(content)) as archive:
        if sum(item.file_size for item in archive.infolist()) > OZON_STORAGE.max_unpacked_bytes:
            raise ValueError("Storage report unpacked limit exceeded")
        sheets = tuple(
            name
            for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        if len(sheets) != 1:
            raise ValueError("Unexpected storage report sheets")

        def xml(name: str) -> bytes:
            value = archive.read(name)
            if b"<!DOCTYPE" in value.upper() or b"<!ENTITY" in value.upper():
                raise ValueError("Unsupported storage report XML")
            return value

        shared: tuple[str, ...] = ()
        workbook = ElementTree.fromstring(xml("xl/workbook.xml"))
        properties = workbook.find(ns + "workbookPr")
        if properties is not None and properties.get("date1904") in ("1", "true"):
            raise ValueError("Unexpected storage report date epoch")
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = tuple(
                "".join(item.itertext()) for item in ElementTree.fromstring(xml("xl/sharedStrings.xml"))
            )
        columns: dict[str, int] = {}
        for _, element in ElementTree.iterparse(BytesIO(xml(sheets[0])), events=("end",)):
            if element.tag != ns + "row":
                continue
            values: dict[int, str] = {}
            column = 0
            for cell in element.findall(ns + "c"):
                if cell.find(ns + "f") is not None:
                    raise ValueError("Unexpected formula in storage report")
                reference = "".join(char for char in cell.get("r", "") if char.isalpha())
                if reference:
                    column = 0
                    for char in reference:
                        column = column * 26 + ord(char) - ord("A") + 1
                else:
                    column += 1
                raw = cell.findtext(ns + "v", "")
                kind = cell.get("t", "")
                if kind == "s":
                    index = int(raw)
                    if index < 0 or index >= len(shared):
                        raise ValueError("Invalid storage shared string")
                    raw = shared[index]
                elif kind == "inlineStr":
                    raw = "".join(cell.itertext())
                elif kind in ("e", "b"):
                    raise ValueError("Invalid storage cell type")
                values[column] = raw.strip()
            element.clear()
            if not any(values.values()):
                continue
            if not columns:
                for label in required:
                    matches = [column for column, value in values.items() if value == label]
                    if len(matches) != 1:
                        raise ValueError("Storage report header changed")
                    columns[label] = matches[0]
                continue
            selected = [values.get(columns[label], "") for label in required]
            raw_day, sku, article, raw_cost = selected
            day = (
                date.fromisoformat(OZON_STORAGE.excel_epoch) + timedelta(days=int(raw_day))
                if raw_day.isdecimal()
                else datetime.fromisoformat(raw_day).date()
            )
            if not period.start <= day <= period.end or not sku.isdecimal() or not article or not raw_cost:
                raise ValueError("Invalid storage report identity or date")
            expense = Decimal(raw_cost.replace("\u00a0", "").replace(" ", "").replace(",", "."))
            rows.append(StorageRow(day=day, sku=sku, article=article, expenses=expense))
            if len(rows) > OZON_STORAGE.max_rows:
                raise ValueError("Storage report row limit exceeded")
        if not columns:
            raise ValueError("Missing storage report header")
    return StorageReport(rows=tuple(rows), sha256=sha256(content).hexdigest())


def normalize(
    store: str, evidence: StorageEvidence, articles: tuple[str, ...], observed_at: datetime
) -> StorageStoreSnapshot:
    period = evidence.period
    days = tuple(period.start + timedelta(days=offset) for offset in range(OZON_STORAGE.window_days))
    report_days = tuple(sorted({row.day for row in evidence.report.rows}))
    if any(day not in days for day in report_days) or any(sale.day not in days for sale in evidence.sales):
        raise ValueError("Storage evidence outside period")
    identities: dict[str, str] = {}
    for sku, article in (
        *((row.sku, row.article) for row in evidence.report.rows),
        *((item.sku, item.article) for item in evidence.articles),
    ):
        if not sku or not article or identities.setdefault(sku, article) != article:
            raise ValueError("Ambiguous storage SKU mapping")
    expenses: dict[tuple[str, date], Decimal] = defaultdict(Decimal)
    counts: dict[tuple[str, date], int] = defaultdict(int)
    sold: dict[tuple[str, date], int] = defaultdict(int)
    returned: dict[tuple[str, date], int] = defaultdict(int)
    for row in evidence.report.rows:
        key = (row.article, row.day)
        expenses[key] += row.expenses
        counts[key] += 1
    for sale in evidence.sales:
        sale_article = identities.get(sale.sku)
        if sale_article is None:
            raise ValueError("Unmapped storage sale SKU")
        key = (sale_article, sale.day)
        sold[key] += sale.sold
        returned[key] += sale.returned
    products: list[StorageSnapshot] = []
    complete = report_days == days
    for article in sorted(set(articles) | set(identities.values())):
        daily = tuple(
            StorageDay(
                day=day,
                expenses=expenses[article, day],
                sold_units=sold[article, day],
                returned_units=returned[article, day],
                report_rows=counts[article, day],
            )
            for day in days
        )
        expense = sum((day.expenses for day in daily), Decimal(0))
        sold_units = sum(day.sold_units for day in daily)
        returned_units = sum(day.returned_units for day in daily)
        retained = sold_units - returned_units
        state = OzonStorageState.AVAILABLE if retained > 0 else OzonStorageState.INSUFFICIENT
        if not complete:
            state = OzonStorageState.PARTIAL
        products.append(
            StorageSnapshot(
                store_slug=store,
                article=article,
                period=period,
                observed_at=observed_at,
                state=state,
                expenses=expense,
                sold_units=sold_units,
                returned_units=returned_units,
                retained_units=retained,
                per_unit=(expense / retained).quantize(Decimal(OZON_STORAGE.rounding), rounding=ROUND_HALF_UP)
                if complete and retained > 0
                else None,
                days=daily,
                report_sha256=evidence.report.sha256,
                report_days=report_days,
                issues=() if complete else ("incomplete_placement_days",),
            )
        )
    return StorageStoreSnapshot(
        store_slug=store, period=period, observed_at=observed_at, products=tuple(products)
    )
