import re
from collections import Counter, defaultdict
from collections.abc import Sequence

from app import db
from app.core.constants import OZON_SOURCE, Marketplace
from app.core.logging_config import tagged_logger
from app.core.stores import STORES
from app.dto.ozon_source import (
    OzonSourceCatalogIndex,
    OzonSourceCatalogItem,
    OzonSourceCell,
    OzonSourceHeader,
    OzonSourceMatch,
    OzonSourceReport,
    OzonSourceSelection,
    OzonSourceSheet,
    OzonSourceTag,
    OzonSourceValues,
)
from app.economics.sources import purchase_prices as source
from app.repositories import unit_economics_1c as repository

logger = tagged_logger(__name__, "economics", "sources", "ozon")
MARKETPLACE = Marketplace.OZON.value


def _find_header(rows: tuple[tuple[OzonSourceCell, ...], ...]) -> OzonSourceHeader:
    required = {column for _, column in OZON_SOURCE.columns}
    for index, row in enumerate(rows[: OZON_SOURCE.header_search_rows]):
        columns = {source._header_key(value): offset for offset, value in enumerate(row)}
        if required.issubset(columns):
            return OzonSourceHeader(index=index, columns=columns)
    raise source.SourceDataError(
        "В листе OZON не найдены колонки: Артикул ОЗОН, Тег, Себес, руб, Проч.затр, руб, ДРР %"
    )


def _parse_tag(value: OzonSourceCell) -> OzonSourceTag:
    raw = source._text(value)
    if not raw or raw.upper() in OZON_SOURCE.empty_tags:
        raw = ""
    main, separator, extra = raw.partition("|")
    tag = source._split_tag(main)
    parts = [source._text(part) for part in main.split("/")]
    if len(parts) >= 4 and re.match(OZON_SOURCE.week_pattern, parts[2], re.IGNORECASE):
        tag["stock_status"] = parts[3] or None
        tag["stock_end_week"] = parts[2] or None
    external = source._split_supplier_external(extra if separator else "")
    return OzonSourceTag.model_validate({**tag, **external, "tag_raw": raw or None})


def _catalog_index(catalog: Sequence[OzonSourceCatalogItem]) -> OzonSourceCatalogIndex:
    by_product_id: dict[str, list[OzonSourceCatalogItem]] = defaultdict(list)
    by_sku: dict[str, list[OzonSourceCatalogItem]] = defaultdict(list)
    for item in catalog:
        product_id = source._identifier(item.mp_product_id)
        sku = source._identifier(item.mp_sku)
        if product_id:
            by_product_id[product_id].append(item)
        if sku:
            by_sku[sku].append(item)
    return OzonSourceCatalogIndex(by_product_id=by_product_id, by_sku=by_sku)


def _match_item(
    identifier: str,
    catalog: OzonSourceCatalogIndex,
    sheet_stores: set[str],
) -> OzonSourceMatch:
    candidates = catalog.by_product_id.get(identifier, [])
    by_sku = not candidates
    if by_sku:
        candidates = catalog.by_sku.get(identifier, [])
    if len(candidates) > 1:
        scoped = [item for item in candidates if item.store_slug in sheet_stores]
        if scoped:
            candidates = scoped
    return OzonSourceMatch(candidates=tuple(candidates), by_sku=by_sku)


def _select_source(candidates: Sequence[OzonSourceSelection]) -> OzonSourceSelection:
    with_fulfillment = [candidate for candidate in candidates if (candidate.values.fulfillment_cost or 0) > 0]
    preferred = with_fulfillment or candidates
    selected = preferred[0]
    for candidate in preferred[1:]:
        if not selected.values.equivalent_to(candidate.values):
            raise source.SourceDataError(
                f"Разные данные Ozon для артикула {candidate.identifier}: "
                f"{selected.values.source_sheet_title}:{selected.values.source_row} и "
                f"{candidate.values.source_sheet_title}:{candidate.values.source_row}"
            )
    return selected


def _number(value: OzonSourceCell) -> float | None:
    return source._number(str(value))


def parse_source_values(
    sheets: Sequence[OzonSourceSheet], catalog: Sequence[OzonSourceCatalogItem]
) -> OzonSourceReport:
    index = _catalog_index(catalog)
    source_columns = dict(OZON_SOURCE.columns)
    candidates_by_item: dict[int, list[OzonSourceSelection]] = defaultdict(list)
    commissions_by_store: dict[str, Counter[float]] = defaultdict(Counter)
    source_rows = skipped_not_ozon = unmatched = ambiguous = 0
    unmatched_examples: list[str] = []

    for sheet in sheets:
        title = source._text(sheet.title)
        sheet_stores = source._sheet_store_slugs(title)
        header = _find_header(sheet.rows)
        for source_row, row in enumerate(sheet.rows[header.index + 1 :], start=header.index + 2):

            def cell(
                name: str,
                current_row: tuple[OzonSourceCell, ...] = row,
                current_columns: dict[str, int] = header.columns,
            ) -> OzonSourceCell:
                column = current_columns[source_columns[name]]
                return current_row[column] if column < len(current_row) else None

            identifier = source._identifier(cell("product_id"))
            if not identifier:
                continue
            if source._header_key(identifier) == OZON_SOURCE.not_ozon_key:
                skipped_not_ozon += 1
                continue
            source_rows += 1
            match = _match_item(identifier, index, sheet_stores)
            if not match.candidates:
                unmatched += 1
                if len(unmatched_examples) < OZON_SOURCE.unmatched_example_limit:
                    unmatched_examples.append(identifier)
                continue
            if len(match.candidates) > 1:
                ambiguous += 1
                continue
            item = match.candidates[0]
            manager_column = header.columns.get("менеджер")
            parsed = OzonSourceValues(
                stock_item_id=item.id,
                manager=source._text(row[manager_column]) or None
                if manager_column is not None and manager_column < len(row)
                else None,
                purchase_price=_number(cell("purchase_price")),
                fulfillment_cost=_number(cell("fulfillment_cost")),
                team_commission_percent=_number(cell("team_commission")),
                source_sheet_id=sheet.sheet_id,
                source_sheet_title=title,
                source_row=source_row,
                **_parse_tag(cell("tag")).model_dump(),
            )
            candidates_by_item[item.id].append(
                OzonSourceSelection(
                    values=parsed,
                    identifier=identifier,
                    store_slug=item.store_slug,
                    by_sku=match.by_sku,
                    outside_sheet_store=item.store_slug not in sheet_stores,
                )
            )

    selected_rows = tuple(_select_source(candidates) for candidates in candidates_by_item.values())
    for selected in selected_rows:
        commission = selected.values.team_commission_percent
        if commission is not None:
            commissions_by_store[selected.store_slug][commission] += 1

    return OzonSourceReport(
        rows=tuple(selected.values for selected in selected_rows),
        team_commissions={
            store_slug: counts.most_common(1)[0][0]
            for store_slug, counts in commissions_by_store.items()
            if counts
        },
        sheet_count=len(sheets),
        source_rows=source_rows,
        skipped_not_ozon=skipped_not_ozon,
        matched=len(selected_rows),
        unmatched=unmatched,
        unmatched_examples=tuple(unmatched_examples),
        ambiguous=ambiguous,
        duplicates=sum(len(candidates) - 1 for candidates in candidates_by_item.values()),
        sku_matched=sum(selected.by_sku for selected in selected_rows),
        outside_sheet_store_matched=sum(selected.outside_sheet_store for selected in selected_rows),
        commission_conflicts={
            store_slug: dict(sorted(counts.items()))
            for store_slug, counts in commissions_by_store.items()
            if len(counts) > 1
        },
    )


def sync_all(sheets: list[dict[str, object]] | None = None) -> dict[str, object]:
    now = source._now()
    try:
        loaded_sheets = sheets if sheets is not None else source.fetch_sheet_rows(sheet_suffix=MARKETPLACE)
        report = parse_source_values(
            tuple(OzonSourceSheet.model_validate(sheet) for sheet in loaded_sheets),
            repository.list_ozon_source_items(),
        )
        saved = repository.replace_source_values(
            report.rows, report.team_commissions, now, marketplace=MARKETPLACE
        )
    except Exception as error:
        for slug in STORES:
            db.record_sync_health(slug, MARKETPLACE, "unit_economics_1c_source", False, str(error), now)
        raise
    for slug in STORES:
        db.record_sync_health(slug, MARKETPLACE, "unit_economics_1c_source", True, None, now)
    logger.info(
        "ozon_source_sync sheets=%s saved=%s skipped_not_ozon=%s unmatched=%s sku=%s outside_sheet_store=%s",
        report.sheet_count,
        saved,
        report.skipped_not_ozon,
        report.unmatched,
        report.sku_matched,
        report.outside_sheet_store_matched,
    )
    return {
        "ok": True,
        "saved": saved,
        "synced_at": now,
        **report.model_dump(mode="json", exclude={"rows", "team_commissions"}),
    }
