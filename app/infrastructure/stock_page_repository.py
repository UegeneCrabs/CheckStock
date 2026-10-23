from collections.abc import Mapping
from datetime import date, timedelta

from app import db
from app.config import settings
from app.core import health
from app.core.constants import WEB, Marketplace
from app.dto.common import DtoModel
from app.dto.stock_pages import (
    ArticleTransit,
    ArticleWarehouse,
    CatalogProduct,
    CostReportData,
    CostSale,
    CostSummary,
    FbsFormula,
    FbsQuantities,
    FulfillmentQuantities,
    OperationData,
    OperationSummary,
    StockArticleDetail,
    StockOperation,
    StockOverview,
    StockOverviewStore,
    StockPageQuery,
    StockProduct,
    StockStoreData,
    SupplyPageData,
    TotalStockData,
    TotalStockRow,
    TransitQuantities,
    WarehouseData,
    WarehouseQuantity,
)
from app.infrastructure.async_read_models import AsyncReadModels
from app.stock import cost_report, supply_planning, total
from app.stock.schemes import fbs_schemes_for, scheme_keys_for
from app.yandex import sync as ya_sync


def project[T: DtoModel](model: type[T], row: Mapping[str, object]) -> T:
    return model.model_validate({name: row[name] for name in model.model_fields if name in row})


class StockPageRepository(AsyncReadModels):
    async def overview(self, query: StockPageQuery) -> StockOverview:
        def load() -> StockOverview:
            rows = db.get_stock_overview(
                tuple((target.store_slug, target.marketplace) for target in query.targets)
            )
            stores = tuple(
                StockOverviewStore(
                    store_slug=slug,
                    **{
                        field: rows.get(slug, {}).get(field, () if field == "marketplaces" else 0)
                        for field in ("total_stock", "sku_count", "marketplaces", "marketplace_count")
                    },
                )
                for slug in query.stores
            )
            return StockOverview(
                stores=stores,
                last_sync=db.get_last_sync_at(),
                total_stock=sum(row.total_stock for row in stores),
                total_sku=sum(row.sku_count for row in stores),
                total_connections=sum(row.marketplace_count for row in stores),
            )

        return await self.read(load)

    async def stock(self, query: StockPageQuery) -> StockStoreData:
        def load() -> StockStoreData:
            schemes = scheme_keys_for(query.marketplace, query.store)
            transit = db.get_ff_transit_totals(query.store, query.marketplace)
            items = []
            for row in db.get_stock_items(query.store, query.marketplace, schemes):
                quantities = {scheme: int(row.get(f"{scheme}_stock") or 0) for scheme in schemes}
                ff = int(row.get("ff_available") or 0)
                in_transit = int(transit.get(row["article"], 0))
                items.append(
                    StockProduct(
                        **project(CatalogProduct, row).model_dump(),
                        ff_available=ff,
                        transit=in_transit,
                        schemes=quantities,
                        total=ff + in_transit + sum(quantities.values()),
                    )
                )
            totals = {scheme: sum(item.schemes[scheme] for item in items) for scheme in schemes}
            totals.update(ff=sum(item.ff_available for item in items), transit=sum(transit.values()))
            totals["grand"] = totals["ff"] + totals["transit"] + sum(totals[scheme] for scheme in schemes)
            return StockStoreData(
                store_slug=query.store,
                marketplace=query.marketplace,
                can_edit=query.can_edit,
                ready=health.has_token(query.marketplace, query.store),
                schemes=schemes,
                fulfillments=tuple(db.get_fulfillments()),
                items=tuple(items),
                totals=totals,
            )

        return await self.read(load)

    async def warehouses(self, query: StockPageQuery) -> WarehouseData:
        def load() -> WarehouseData:
            return WarehouseData(
                store_slug=query.store,
                marketplace=query.marketplace,
                can_edit=query.can_edit,
                fbo=tuple(
                    project(WarehouseQuantity, row)
                    for row in db.get_mp_warehouse_details(query.store, query.marketplace, "fbo")
                ),
                fbs=tuple(
                    project(WarehouseQuantity, row)
                    for row in db.get_mp_fbs_warehouse_details(query.store, query.marketplace)
                ),
                fulfillment=tuple(
                    project(WarehouseQuantity, row)
                    for row in db.get_ff_warehouse_details_by_mp(query.store, query.marketplace)
                ),
                trash=tuple(
                    project(WarehouseQuantity, row)
                    for row in db.get_trash_details(query.store, query.marketplace)
                ),
            )

        return await self.read(load)

    async def totals(self, query: StockPageQuery) -> TotalStockData:
        def load() -> TotalStockData:
            source = total.build_rows(
                query.stores,
                tuple((target.store_slug, target.marketplace) for target in query.targets),
                query.store,
            )
            rows = tuple(
                project(
                    TotalStockRow,
                    {**row, "quantities": {key: int(row.get(key) or 0) for key in total.VALUE_KEYS}},
                )
                for row in source
            )
            return TotalStockData(
                store=query.store,
                rows=rows,
                value_keys=total.VALUE_KEYS,
                totals={key: sum(row.quantities[key] for row in rows) for key in total.VALUE_KEYS},
            )

        return await self.read(load)

    async def operations(
        self, query: StockPageQuery, kinds: tuple[str, ...] | None, kind: str, page: int
    ) -> OperationData:
        def load() -> OperationData:
            marketplaces = tuple(
                target.marketplace for target in query.targets if target.store_slug == query.store
            )
            stats = db.get_store_operation_stats(query.store, kinds, marketplaces=marketplaces)
            summary = OperationSummary.model_validate(stats["summary"])
            size = settings.operation_history_limit
            selected = min(page, max(1, (summary.total + size - 1) // size))
            rows = db.get_store_operations(
                query.store, kinds, size, offset=(selected - 1) * size, marketplaces=marketplaces
            )
            return OperationData(
                store_slug=query.store,
                kind=kind,
                page=selected,
                page_size=size,
                counts=stats["counts"],
                summary=summary,
                operations=tuple(project(StockOperation, row) for row in rows),
            )

        return await self.read(load)

    async def costs(self, query: StockPageQuery, start: date, end: date, current: date) -> CostReportData:
        def load() -> CostReportData:
            marketplaces = (
                (query.marketplace,)
                if query.marketplace
                else tuple(dict.fromkeys(target.marketplace for target in query.targets))
            )
            report = cost_report.build_report(
                (query.store,) if query.store else query.stores,
                start,
                end,
                marketplaces,
                tuple((target.store_slug, target.marketplace) for target in query.targets),
            )
            monday = current - timedelta(days=current.weekday())
            return CostReportData(
                date_from=start,
                date_to=end,
                current_from=monday,
                current_to=current,
                previous_from=monday - timedelta(days=WEB.week_days),
                previous_to=monday - timedelta(days=1),
                can_edit=query.can_edit,
                summary=tuple(
                    project(
                        CostSummary,
                        {
                            **row,
                            "fbs_formula": project(FbsFormula, row["fbs_formula"])
                            if row["fbs_formula"]
                            else None,
                        },
                    )
                    for row in report["summary"]
                ),
                operations=tuple(project(StockOperation, row) for row in report["operations"]),
                fbs_sales=tuple(project(CostSale, row) for row in report["fbs_sales"]),
                fbs_actual_sales=tuple(project(CostSale, row) for row in report["fbs_actual_sales"]),
            )

        return await self.read(load)

    async def supplies(self) -> SupplyPageData:
        return SupplyPageData.model_validate(supply_planning.planned_supply_date_bounds())

    async def fbs(self, query: StockPageQuery, fulfillment: str) -> FbsQuantities:
        def load() -> FbsQuantities:
            def quantities(scheme: str) -> dict[str, int]:
                if fulfillment:
                    return db.get_mp_stock_by_warehouse(query.store, query.marketplace, scheme, fulfillment)
                return db.get_mp_stock_totals(query.store, query.marketplace, scheme)

            fbs: dict[str, int] = {}
            for scheme in fbs_schemes_for(query.marketplace, query.store):
                for article, quantity in quantities(scheme).items():
                    fbs[article] = fbs.get(article, 0) + int(quantity or 0)
            return FbsQuantities(
                fbs=fbs, rfbs=quantities("rfbs") if query.marketplace == Marketplace.OZON else {}
            )

        return await self.read(load)

    async def fulfillment(self, query: StockPageQuery, fulfillment: str) -> FulfillmentQuantities:
        return await self.read(
            lambda: FulfillmentQuantities(
                ff_available=db.get_ff_available_totals(query.store, fulfillment or None, query.marketplace)
            )
        )

    async def transit(self, query: StockPageQuery, fulfillment: str) -> TransitQuantities:
        return await self.read(
            lambda: TransitQuantities(
                transit=db.get_ff_transit_totals(query.store, query.marketplace, fulfillment or None)
            )
        )

    async def article(self, query: StockPageQuery, article: str) -> StockArticleDetail:
        def load() -> StockArticleDetail:
            fulfillments = db.get_fulfillments()
            fbs_by_warehouse: dict[str, int] = {}
            yandex_names = (
                ya_sync.known_ff_by_campaign() if query.marketplace == Marketplace.YANDEX_MARKET else {}
            )
            for row in db.get_mp_fbs_warehouse_details(query.store, query.marketplace, article):
                warehouse = str(row["warehouse"])
                if yandex_names:
                    warehouse = yandex_names.get(ya_sync._normalize_ff_name(warehouse), warehouse)
                fbs_by_warehouse[warehouse] = fbs_by_warehouse.get(warehouse, 0) + int(row["quantity"] or 0)
            warehouses = [
                ArticleWarehouse(
                    name=fulfillment,
                    available=int(
                        db.get_ff_available_totals(query.store, fulfillment, query.marketplace).get(
                            article, 0
                        )
                    ),
                    fbs=fbs_by_warehouse.pop(fulfillment, 0),
                )
                for fulfillment in fulfillments
            ]
            warehouses.extend(
                ArticleWarehouse(name=warehouse, available=0, fbs=quantity)
                for warehouse, quantity in sorted(fbs_by_warehouse.items())
            )
            batches = db.get_ff_transit_batches(query.store, query.marketplace, active_only=True)
            transit = tuple(
                ArticleTransit(
                    transfer_id=batch["id"],
                    from_fulfillment=batch["from_fulfillment"],
                    to_fulfillment=batch["to_fulfillment"],
                    sent_at=batch["sent_at"],
                    status=batch["status"],
                    quantity=item["remaining_quantity"],
                )
                for batch in batches
                for item in batch["items"]
                if item["to_article"] == article and int(item["remaining_quantity"] or 0) > 0
            )
            return StockArticleDetail(
                article=article, marketplace=query.marketplace, warehouses=tuple(warehouses), transit=transit
            )

        return await self.read(load)
