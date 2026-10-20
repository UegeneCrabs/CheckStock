"""Normalize archived WB JSON once before calculation; retain raw documents separately."""

import math
from dataclasses import dataclass

from app.dto.wb_standard_costs import SourceBundle
from app.economics.wb.standard_numbers import number


@dataclass(frozen=True)
class FinanceEntry:
    row_id: int
    article: str
    day: str
    operation: str | None
    warehouse: str | None
    quantity: float | None
    price: float | None
    currency: str | None
    title: str | None = None
    vendor: str | None = None
    subject: str | None = None


@dataclass(frozen=True)
class StorageEntry:
    article: str
    day: str
    warehouse: str | None
    quantity: float | None
    volume: float | None
    amount: float | None
    discount: float | None
    pallets: float | None
    calc_type: str | None
    title: str | None = None
    vendor: str | None = None
    subject: str | None = None


@dataclass(frozen=True)
class ProductCard:
    article: str = ""
    subject_id: str = ""
    title: str | None = None
    vendor: str | None = None
    subject: str | None = None
    volume: float | None = None


@dataclass(frozen=True)
class CommissionRate:
    subject_id: str = ""
    subject: str | None = None
    fbw: float | None = None
    fbs: float | None = None


@dataclass(frozen=True)
class WarehouseTariff:
    name: str | None = None
    fbw_base: float | None = None
    fbw_extra: float | None = None
    fbs_base: float | None = None
    fbs_extra: float | None = None


@dataclass(frozen=True)
class DailyTariffs:
    day: str
    currency: str | None
    warehouses: tuple[WarehouseTariff, ...]


@dataclass(frozen=True)
class NormalizedSources:
    finance: tuple[FinanceEntry, ...]
    storage: tuple[StorageEntry, ...]
    cards: tuple[ProductCard, ...]
    commissions: tuple[CommissionRate, ...]
    tariffs: tuple[DailyTariffs, ...]


def normalize_sources(raw: SourceBundle) -> NormalizedSources:
    finance = tuple(
        FinanceEntry(
            row_id=int(s["rrdId"]),
            article=str(s["nmId"]),
            day=str(s.get("rrDate", ""))[:10],
            operation=s.get("sellerOperName"),
            warehouse=s.get("officeName"),
            quantity=number(s.get("quantity")),
            price=number(s.get("retailPriceWithDisc")),
            currency=s.get("currency"),
            title=s.get("title"),
            vendor=s.get("vendorCode"),
            subject=s.get("subjectName") or s.get("subject"),
        )
        for s in raw.finance
    )
    storage = tuple(
        StorageEntry(
            article=str(s["nmId"]),
            day=str(s.get("date", ""))[:10],
            warehouse=s.get("warehouse"),
            quantity=number(s.get("barcodesCount")),
            volume=number(s.get("volume")),
            amount=number(s.get("warehousePrice")),
            discount=number(s.get("loyaltyDiscount")),
            pallets=number(s.get("palletCount")),
            calc_type=s.get("calcType"),
            title=s.get("title"),
            vendor=s.get("vendorCode"),
            subject=s.get("subjectName") or s.get("subject"),
        )
        for s in raw.storage
    )
    cards = []
    for card in raw.cards:
        dimensions = card.get("dimensions") or {}
        sides = [number(dimensions.get(k)) for k in ("length", "width", "height")]
        volume = math.prod(sides) / 1000 if all(v is not None and v > 0 for v in sides) else None
        cards.append(
            ProductCard(
                str(card["nmID"]),
                str(card.get("subjectID")),
                card.get("title"),
                card.get("vendorCode"),
                card.get("subjectName"),
                volume,
            )
        )
    commissions = tuple(
        CommissionRate(
            str(f["subjectID"]),
            f.get("subjectName"),
            number(f.get("paidStorageKgvp")),
            number(f.get("kgvpMarketplace")),
        )
        for f in raw.commissions["report"]
    )
    tariffs = []
    for day, response in raw.tariffs.items():
        data = response["response"]["data"]
        warehouses = tuple(
            WarehouseTariff(
                t["warehouseName"],
                number(t.get("boxDeliveryBase")),
                number(t.get("boxDeliveryLiter")),
                number(t.get("boxDeliveryMarketplaceBase")),
                number(t.get("boxDeliveryMarketplaceLiter")),
            )
            for t in data["warehouseList"]
        )
        tariffs.append(DailyTariffs(day, data.get("currency"), warehouses))
    return NormalizedSources(finance, storage, tuple(cards), commissions, tuple(tariffs))
