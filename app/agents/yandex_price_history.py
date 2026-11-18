from dataclasses import dataclass
from datetime import date
from math import isfinite

from app.repositories import yandex_economics
from app.yandex.economics_shared import SCHEME, SOURCE_SCHEMES


@dataclass(frozen=True, slots=True)
class DailyPrices:
    article: str
    name: str | None
    day: str
    captured_at: str | None
    seller_price: float | None
    buyer_price: float | None
    pay_price: float | None
    seller_price_origin: str | None
    buyer_price_origin: str | None
    pay_price_origin: str | None


@dataclass(frozen=True, slots=True)
class PriceValue:
    amount: float | None
    origin: str | None


def price(data: dict, field: str) -> PriceValue:
    values = data.get("inputs") or data.get("values") or {}
    value = values.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        return PriceValue(amount=None, origin=None)
    origins = data.get("origins") or {}
    origin = origins.get(field)
    pricing = data.get("pricing") or {}
    if origin is None and field in {"buyer_price", "pay_price"}:
        estimated_key = "buyer_estimated" if field == "buyer_price" else "pay_estimated"
        if pricing.get(estimated_key) is True:
            origin = "Расчётная цена"
    return PriceValue(amount=float(value), origin=origin if isinstance(origin, str) else None)


def history(store: str, start: date, end: date, names: dict[str, str | None]) -> list[DailyPrices]:
    if not names:
        return []
    saved = yandex_economics.history(store, start.isoformat(), end.isoformat(), articles=tuple(names))
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in saved:
        if row["article"] in names and row["scheme"] in (SCHEME, *SOURCE_SCHEMES):
            groups.setdefault((row["article"], row["day"]), []).append(row)
    result = []
    for (article, day), candidates in sorted(groups.items()):
        common = [row for row in candidates if row["scheme"] == SCHEME]
        selected = common or candidates
        fields = {}
        for field in ("seller_price", "buyer_price", "pay_price"):
            observed = [price(row["data"], field) for row in selected]
            values = {item.amount for item in observed if item.amount is not None}
            value = next(iter(values)) if len(values) == 1 else None
            origins = {item.origin for item in observed if item.amount == value}
            origin = next(iter(origins)) if value is not None and len(origins) == 1 else None
            fields[field] = value
            fields[field + "_origin"] = origin
        stamps = {row.get("captured_at") for row in selected if row.get("captured_at")}
        result.append(
            DailyPrices(
                article, names[article], day, next(iter(stamps)) if len(stamps) == 1 else None, **fields
            )
        )
    return result
