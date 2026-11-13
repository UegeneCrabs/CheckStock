from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.core.constants import INBOUND_SCHEDULE
from app.core.domain import MOSCOW_TIMEZONE
from app.dto.inbound_supplies import TERMINAL_STAGES, InboundItem, InboundSnapshot
from app.repositories.stock_transit import get_ff_transit_totals
from app.stock import inbound_supplies


@dataclass(frozen=True)
class InboundExport:
    catalog: list[dict]
    quantities: dict[str, int | None]
    available: bool
    warnings: tuple[str, ...] = ()
    confirmed_quantities: dict[str, int] = field(default_factory=dict)


def load_accounted(store_slug: str, marketplace: str, catalog: list[dict]) -> InboundExport:
    products = {str(row["article"]): dict(row) for row in catalog if row.get("article")}
    all_transit = get_ff_transit_totals(store_slug, marketplace)
    between_ff = get_ff_transit_totals(store_slug, marketplace, include_fbo=False)
    quantities = dict.fromkeys(products, 0)
    for article, total in all_transit.items():
        quantity = total - between_ff.get(article, 0)
        if quantity:
            products.setdefault(article, {"article": article, "name": article, "barcode": ""})
            quantities[article] = quantity
    return InboundExport(list(products.values()), quantities, True, confirmed_quantities=dict(quantities))


def _key(value: object) -> str:
    return str(value or "").strip().casefold()


def summarize(
    snapshot: InboundSnapshot,
    catalog: list[dict],
    now: datetime | None = None,
    *,
    allow_saved_snapshot: bool = False,
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
    cutoff = current - timedelta(seconds=INBOUND_SCHEDULE.stale_after_seconds)
    fresh = bool(
        updated
        and updated >= cutoff
        and snapshot.status in {"ok", "partial", "running"}
        and not snapshot.error
    )
    # Failed refreshes retain the last successful payload and its timestamp.
    # Reuse is opt-in: pricing calculations still require the strict fresh snapshot.
    available = fresh or (allow_saved_snapshot and updated is not None)
    using_saved = allow_saved_snapshot and updated is not None and (not fresh or snapshot.status == "running")
    quantities: dict[str, int | None] = dict.fromkeys(products, 0)
    confirmed_quantities: dict[str, int] = defaultdict(int)
    for supply in snapshot.supplies:
        if not supply.listed_status or supply.stage in TERMINAL_STAGES:
            continue
        if not supply.composition_confirmed and supply.stage == "planned" and not supply.items:
            continue
        if not supply.composition_confirmed or (supply.unavailable and not supply.items):
            available = False
        for item in supply.items:
            remaining = supply.remaining_for(item)
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
        snapshot_description = "подтверждённого сохранённого" if allow_saved_snapshot else "свежего"
        warnings = (
            f"Нет полного {snapshot_description} снимка поставок МП. "
            "Столбцы «В пути на склады МП» и «ТОТАЛ» оставлены пустыми.",
        )
    else:
        messages = []
        if using_saved:
            saved_at = updated.astimezone(MOSCOW_TIMEZONE).strftime("%d.%m.%Y %H:%M:%S")
            messages.append(
                f"Использован последний сохранённый снимок поставок МП от {saved_at} МСК. "
                "Свежие данные пока не подтверждены."
            )
        if any(value is None for value in quantities.values()):
            messages.append(
                "Часть количеств в поставках МП не подтверждена. Для этих артикулов ячейки "
                "«В пути на склады МП» и «ТОТАЛ» оставлены пустыми."
            )
        warnings = tuple(messages)
    return InboundExport(list(products.values()), quantities, available, warnings, dict(confirmed_quantities))


def load(
    store_slug: str,
    marketplace: str,
    catalog: list[dict],
    *,
    now: datetime | None = None,
    allow_saved_snapshot: bool = False,
) -> InboundExport:
    snapshot = inbound_supplies.build_service().report(((store_slug, marketplace),))[0]
    return summarize(
        snapshot,
        catalog,
        now,
        allow_saved_snapshot=allow_saved_snapshot,
    )
