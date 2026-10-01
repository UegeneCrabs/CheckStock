"""Parse product rows from Ozon Performance API CSV and ZIP reports."""

import csv
import io
import re
import zipfile
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from app.ozon.performance import PerformanceApiError


def _number(value: str) -> Decimal:
    raw = str(value or "").strip()
    cleaned = re.sub(r"[^0-9,.-]", "", raw)
    if raw and not cleaned:
        raise PerformanceApiError(f"В отчёте Ozon некорректное число: {raw[:40]}")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return Decimal(cleaned or "0")
    except InvalidOperation as error:
        raise PerformanceApiError(f"В отчёте Ozon некорректное число: {raw[:40]}") from error


def _fields(record: list[str]) -> list[str]:
    fields = record
    if len(fields) == 1 and ";" in fields[0]:
        fields = next(csv.reader([fields[0]], delimiter=";"), [])
    return [str(field).strip() for field in fields]


def _text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise PerformanceApiError("Не удалось прочитать кодировку отчёта Ozon")


def _report_files(raw: bytes) -> list[bytes]:
    if not raw.startswith(b"PK"):
        return [raw]
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = [
            name for name in archive.namelist()
            if name.lower().endswith(".csv") and not name.startswith("__MACOSX/")
        ]
        return [archive.read(name) for name in names]


def _column(headers: list[str], prefix: str, *, excluded: str = "") -> int | None:
    return next(
        (
            index for index, label in enumerate(headers)
            if label.startswith(prefix) and (not excluded or excluded not in label)
        ),
        None,
    )


def parse_product_report(raw: bytes, kind: str) -> dict[str, dict]:
    if kind not in {"cpc", "cpo", "all_promo"}:
        raise ValueError("Неизвестный вид рекламного отчёта")
    totals = defaultdict(lambda: {
        "spend": Decimal(0), "click_spend": Decimal(0), "impressions": 0, "clicks": 0,
        "article": "",
    })
    found_header = False
    for content in _report_files(raw):
        header: list[str] = []
        sku_index = spend_index = views_index = clicks_index = article_index = None
        for record in csv.reader(io.StringIO(_text(content), newline=""), delimiter=";"):
            fields = _fields(record)
            normalized = [field.casefold().strip(' "\ufeff') for field in fields]
            if not header:
                if "sku" not in normalized and not any(field.startswith("sku продвигаемого") for field in normalized):
                    continue
                header = normalized
                found_header = True
                sku_index = (
                    _column(header, "sku продвигаемого") if kind == "all_promo" else None
                )
                if sku_index is None:
                    sku_index = _column(header, "sku")
                spend_index = _column(header, "расход", excluded="оплата за клик")
                article_index = _column(header, "артикул") if kind == "cpo" else None
                views_index = _column(header, "показы")
                clicks_index = _column(header, "клики")
                if sku_index is None or spend_index is None:
                    raise PerformanceApiError("В отчёте Ozon нет колонок SKU и расхода")
                if kind == "cpc" and (views_index is None or clicks_index is None):
                    raise PerformanceApiError("В отчёте Ozon нет показов или кликов")
                continue
            if not fields or sku_index is None or len(fields) <= max(sku_index, spend_index):
                continue
            sku = fields[sku_index].strip()
            if not sku.isdecimal():
                continue
            metric = totals[sku]
            if article_index is not None and len(fields) > article_index:
                metric["article"] = fields[article_index].strip() or metric["article"]
            spent = _number(fields[spend_index])
            metric["spend"] += spent
            if kind == "cpc":
                metric["click_spend"] += spent
                metric["impressions"] += int(_number(fields[views_index]))
                metric["clicks"] += int(_number(fields[clicks_index]))
    if not found_header and raw.strip():
        raise PerformanceApiError("В рекламном отчёте Ozon не найдена таблица товаров")
    return {
        sku: {
            "spend": float(values["spend"]),
            "click_spend": float(values["click_spend"]),
            "impressions": values["impressions"],
            "clicks": values["clicks"],
            "article": values["article"],
        }
        for sku, values in totals.items()
    }
