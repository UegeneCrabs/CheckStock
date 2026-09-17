from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.dto.inbound_supplies import TERMINAL_STAGES, InboundItem, InboundSnapshot, InboundSupply
from app.stock import inbound_supplies


@dataclass(frozen=True)
class InboundExport:
    catalog: list[dict]
    quantities: dict[str, int | None]
    available: bool
    warnings: tuple[str, ...] = ()
    confirmed_quantities: dict[str, int] = field(default_factory=dict)


def _key(value: object) -> str:
    return str(value or "").strip().casefold()


def _remaining_for_export(marketplace: str, supply: InboundSupply, item: InboundItem) -> int | None:
    """Receipt is finished: shortage is not stock in transit. A dispute on another item must not hide this item's confirmed placement balance. Accepted by WB is not necessarily ready for sale in the FBO stock."""
    if supply.unavailable or supply.stage == "unknown":
        return None
    if marketplace == "WB":
        if supply.status == "5":
            if item.accepted_quantity is None or item.ready_quantity is None:
                return None
            return max(item.accepted_quantity - item.ready_quantity, 0)
        if supply.stage == "acceptance":
            if item.ready_quantity is None:
                return None
            return max(item.quantity - item.ready_quantity, 0)
    return supply.model_copy(update={"items": (item,)}).remaining_quantity


def summarize(
    snapshot: InboundSnapshot,
    catalog: list[dict],
    now: datetime | None = None,
    *,
    include_yandex_approved: bool = False,
) -> InboundExport:
    products = {str(row["article"]).strip(): dict(row) for row in catalog if row.get("article")}
    aliases: dict[str, set[str]] = defaultdict(set)
    barcodes: dict[str, set[str]] = defaultdict(set)
    for article, row in products.items():
        for value in (article, article.partition(" / ")[0], row.get("mp_sku"), row.get("mp_product_id")):
            if _key(value):
                aliases[_key(value)].add(article)
        if _key(row.get("barcode")):
            barcodes[_key(row["barcode"])].add(article)

    def articles_for(item: InboundItem) -> set[str]:
        barcode_matches = barcodes.get(_key(item.barcode), set())
        if snapshot.marketplace == "WB" and len(barcode_matches) == 1:
            return barcode_matches
        if item.article in products:
            return {item.article}
        for value in (item.article, item.sku):
            matches = aliases.get(_key(value), set())
            if matches:
                return matches
        if barcode_matches:
            return barcode_matches
        article = item.article or item.sku or item.barcode
        if not article:
            return set()
        products.setdefault(article, {"article": article, "barcode": item.barcode, "name": item.name})
        aliases[_key(article)].add(article)
        return {article}

    try:
        updated = datetime.fromisoformat(snapshot.last_success or "")
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
    except ValueError:
        updated = None
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    cutoff = current - timedelta(seconds=max(settings.inbound_sync_interval_seconds * 2, 3600))
    available = bool(
        updated
        and updated >= cutoff
        and snapshot.status in {"ok", "partial", "running"}
        and not snapshot.error
    )
    quantities: dict[str, int | None] = dict.fromkeys(products, 0)
    confirmed_quantities: dict[str, int] = defaultdict(int)
    for supply in snapshot.supplies:
        wb_accepted = snapshot.marketplace == "WB" and supply.status == "5"
        receipt_finished = (snapshot.marketplace, supply.status) in {
            ("OZON", "COMPLETED"),
            ("YANDEX MARKET", "FINISHED"),
        }
        if not supply.unavailable and (
            (supply.stage in TERMINAL_STAGES and not wb_accepted)
            or (
                supply.stage == "planned"
                and not (
                    include_yandex_approved
                    and snapshot.marketplace == "YANDEX MARKET"
                    and supply.status == "ACCEPTED_BY_WAREHOUSE_SYSTEM"
                )
            )
            or receipt_finished
        ):
            continue
        if not supply.items:
            available = False
        for item in supply.items:
            remaining = _remaining_for_export(snapshot.marketplace, supply, item)
            if remaining == 0:
                continue
            articles = articles_for(item)
            if not articles:
                available = False
                continue
            for article in articles:
                if remaining is not None and len(articles) == 1:
                    confirmed_quantities[article] += remaining
                previous = quantities.get(article, 0)
                quantities[article] = (
                    previous + remaining
                    if previous is not None and remaining is not None and len(articles) == 1
                    else None
                )
    if not available:
        quantities = dict.fromkeys(products, None)
        confirmed_quantities.clear()
        warnings = (
            "Нет полного свежего снимка поставок МП. Столбцы «В пути на склады МП» и «ТОТАЛ» оставлены пустыми.",
        )
    elif any(value is None for value in quantities.values()):
        warnings = (
            "Часть количеств в поставках МП не подтверждена. Для этих артикулов ячейки «В пути на склады МП» и «ТОТАЛ» оставлены пустыми.",
        )
    else:
        warnings = ()
    return InboundExport(list(products.values()), quantities, available, warnings, dict(confirmed_quantities))


def load(
    store_slug: str,
    marketplace: str,
    catalog: list[dict],
    *,
    now: datetime | None = None,
    include_yandex_approved: bool = False,
) -> InboundExport:
    snapshot = inbound_supplies.build_service().report(((store_slug, marketplace),))[0]
    return summarize(snapshot, catalog, now, include_yandex_approved=include_yandex_approved)
