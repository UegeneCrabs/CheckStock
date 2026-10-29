from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.core.constants import (
    OZON_ORDERS,
    TIME,
    AdvertisingKind,
    Marketplace,
    OzonDataIssueCode,
    OzonPriceField,
)
from app.dto.inbound_supplies import InboundSnapshot
from app.dto.ozon_advertising import AdvertisingPeriod, StoreAdvertisingBreakdown
from app.dto.ozon_cabinet import OzonProductGoals
from app.dto.ozon_economics import (
    OzonCoverage,
    OzonDataIssue,
    OzonProduct,
    OzonProducts,
    OzonProductsQuery,
    OzonTurnoverData,
)
from app.dto.ozon_logistics import LogisticsData
from app.dto.ozon_margin import OzonMarginSource
from app.dto.ozon_newness import OzonNewness
from app.dto.ozon_prices import PriceValue
from app.dto.ozon_storage import StorageData
from app.dto.stock_pages import StockTarget
from app.economics import ozon, ozon_commissions, ozon_margin
from app.economics.wb.calculations import calculate_stock_coverage_days
from app.infrastructure.async_advertising_repository import AsyncAdvertisingRepository
from app.infrastructure.async_commissions_repository import AsyncCommissionsRepository
from app.infrastructure.async_database import AsyncDatabase
from app.infrastructure.async_logistics_repository import AsyncLogisticsRepository
from app.infrastructure.async_prices_repository import AsyncPricesRepository
from app.infrastructure.async_read_models import AsyncReadModels
from app.infrastructure.async_storage_repository import AsyncStorageRepository
from app.infrastructure.async_turnover_repository import AsyncTurnoverRepository
from app.infrastructure.inbound_read_repository import InboundReadRepository
from app.infrastructure.ozon_cabinet_repository import OzonCabinetRepository
from app.infrastructure.ozon_margin_repository import OzonMarginRepository
from app.infrastructure.ozon_newness_repository import OzonNewnessRepository
from app.ozon import drr, tokens
from app.ozon.advertising_normalizer import (
    breakdown,
    source_days,
    spend_summary,
    traffic_metrics,
    window_adjustments,
)
from app.ozon.advertising_read_index import AdvertisingReadIndex
from app.ozon.cabinet_normalizer import effective_parameters
from app.ozon.price_normalizer import price_freshness
from app.ozon.turnover_normalizer import period_turnover


def base_products(
    query: OzonProductsQuery,
    snapshots: Mapping[str, InboundSnapshot],
    turnover_data: OzonTurnoverData,
) -> tuple[OzonProduct, ...]:
    products = []
    today = datetime.now(TIME.moscow_timezone).date()
    stock_days = frozenset(today - timedelta(days=offset) for offset in range(OZON_ORDERS.lookback_days))
    for row in ozon.load_products(
        query.stores,
        query.article,
        query.period_from,
        query.period_to,
        inbound_snapshots=snapshots,
        include_legacy_metrics=False,
    ):
        row["stock"].pop("inbound_message", None)
        row["data_issues"] = tuple(
            OzonDataIssue(code=OzonDataIssueCode.SOURCE_ERROR, detail=message)
            for message in row.pop("data_errors")
        )
        value = period_turnover(
            turnover_data, row["store_slug"], row["article"], query.period_from, query.period_to
        )
        row["economics_7d"]["turnover"] = float(value.amount) if value.amount is not None else None
        row["economics_7d"]["turnover_coverage"] = value.coverage
        if not value.coverage.complete:
            row["data_issues"] += (OzonDataIssue(code=OzonDataIssueCode.TURNOVER_UNAVAILABLE),)
        eligible = turnover_data.eligible_days.get(row["store_slug"], frozenset())
        known_days = stock_days & eligible
        stock_coverage = OzonCoverage(
            dates=tuple(day.isoformat() for day in sorted(known_days)),
            days=len(known_days),
            expected_days=len(stock_days),
            complete=stock_days <= eligible,
            missing_dates=tuple(day.isoformat() for day in sorted(stock_days - eligible)),
        )
        count = (
            sum(
                day.ordered
                for day in turnover_data.products.get((row["store_slug"], row["article"]), ())
                if day.day in stock_days
            )
            if stock_coverage.complete
            else None
        )
        row["stock"].update(
            orders_21d=count,
            average_daily_orders=count / OZON_ORDERS.lookback_days if count is not None else None,
            coverage=stock_coverage,
            days=calculate_stock_coverage_days(
                row["stock"]["total"], count, period_days=OZON_ORDERS.lookback_days
            )
            if count is not None and row["stock"]["total"] is not None
            else None,
        )
        if count is None:
            row["data_issues"] += (OzonDataIssue(code=OzonDataIssueCode.ORDERS_UNAVAILABLE),)
        products.append(OzonProduct.model_validate(row))
    return tuple(products)


class OzonEconomicsRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def products(self, query: OzonProductsQuery, last_complete_day: date, custom: bool) -> OzonProducts:
        today = datetime.now(TIME.moscow_timezone).date()
        advertising_week = drr.calculator_advertising_period(today)
        expanded = query.model_copy(
            update={
                "period_to": max(today, query.period_to),
                "period_from": min(query.period_from, today - timedelta(days=OZON_ORDERS.lookback_days - 1)),
            }
        )
        turnover_data = await AsyncTurnoverRepository(self.database).load(expanded)
        snapshots = {
            snapshot.store_slug: snapshot
            for snapshot in await InboundReadRepository(self.database).snapshots(
                tuple(StockTarget(store_slug=slug, marketplace=Marketplace.OZON) for slug in query.stores)
            )
        }
        result = await AsyncReadModels(self.database).read(
            lambda: OzonProducts(
                products=base_products(query, snapshots, turnover_data),
                period_days=(query.period_to - query.period_from).days + 1,
                period_from=query.period_from,
                period_to=query.period_to,
                period_mode="custom" if custom else "preset",
                last_complete_day=last_complete_day,
            )
        )
        period = AdvertisingPeriod(start=query.period_from, end=query.period_to)
        statuses = await AsyncAdvertisingRepository(self.database).overlapping(
            query.stores,
            AdvertisingPeriod(start=min(query.period_from, advertising_week.start), end=expanded.period_to),
        )
        by_store = {
            store: source_days(period, tuple(status for status in statuses if status.store_slug == store))
            for store in query.stores
        }
        today_sources = {
            store: source_days(
                AdvertisingPeriod(start=today, end=today),
                tuple(status for status in statuses if status.store_slug == store),
            )
            for store in query.stores
        }
        configured = {store: tokens.has_performance_credentials(store) for store in query.stores}
        advertising_indexes = {store: AdvertisingReadIndex(sources) for store, sources in by_store.items()}
        today_indexes = {store: AdvertisingReadIndex(sources) for store, sources in today_sources.items()}
        week_indexes = {
            store: AdvertisingReadIndex(
                source_days(
                    advertising_week, tuple(status for status in statuses if status.store_slug == store)
                )
            )
            for store in query.stores
        }
        prices = {
            (item.store_slug, str(item.product_id)): price_freshness(item.prices, datetime.now(UTC))
            for item in await AsyncPricesRepository(self.database).load(query.stores)
        }
        commissions = {
            (item.store_slug, str(item.product_id)): item.value
            for item in await AsyncCommissionsRepository(self.database).load(query.stores)
        }
        logistics_stores = await AsyncLogisticsRepository(self.database).latest(query.stores, today)
        logistics = {
            (store.store_slug, snapshot.article): LogisticsData(
                snapshot=snapshot,
                stale=snapshot.period.day != today or store.error is not None,
                error=store.error,
                attempted_at=store.attempted_at,
            )
            for store in logistics_stores
            for snapshot in store.products
        }
        logistics_missing = {
            store.store_slug: LogisticsData(error=store.error, attempted_at=store.attempted_at)
            for store in logistics_stores
        }
        storage_stores = await AsyncStorageRepository(self.database).latest(query.stores, today)
        storage = {
            (store.store_slug, snapshot.article): StorageData(
                snapshot=snapshot,
                stale=snapshot.period.day != today or store.error is not None,
                error=store.error,
                attempted_at=store.attempted_at,
            )
            for store in storage_stores
            for snapshot in store.products
        }
        storage_missing = {
            store.store_slug: StorageData(error=store.error, attempted_at=store.attempted_at)
            for store in storage_stores
        }
        cabinet_repository = OzonCabinetRepository(self.database)
        cabinets = {item.store_slug: item.values for item in await cabinet_repository.settings(query.stores)}
        buyouts = {item.store_slug: item for item in await cabinet_repository.buyouts(query.stores, today)}
        product_goals = await cabinet_repository.product_goals(query.stores)
        newness = await OzonNewnessRepository(self.database).load(query.stores, today)
        products = tuple(
            product.model_copy(
                update={
                    "newness": (age := newness.get((product.store_slug, product.article), OzonNewness())),
                    "is_new": age.is_new,
                    "sales_days": age.observed_days,
                    "cabinet": (
                        cabinet := effective_parameters(
                            cabinets[product.store_slug],
                            product.article,
                            product.tag_data.code,
                            product_goals.get((product.store_slug, product.article), OzonProductGoals()),
                            buyouts.get(product.store_slug),
                            datetime.now(UTC),
                        )
                    ),
                    "logistics": logistics.get(
                        (product.store_slug, product.article),
                        logistics_missing.get(product.store_slug, LogisticsData()),
                    ),
                    "storage": storage.get(
                        (product.store_slug, product.article),
                        storage_missing.get(product.store_slug, StorageData()),
                    ),
                    "commission": ozon_commissions.snapshot(
                        commissions.get((product.store_slug, product.mp_product_id or "")),
                        prices[(product.store_slug, product.mp_product_id or "")].seller_price
                        if (product.store_slug, product.mp_product_id or "") in prices
                        else PriceValue(),
                        datetime.now(UTC),
                    ),
                    "price": product.price.model_copy(
                        update={field.value: snapshot.value(field) for field in OzonPriceField}
                    )
                    if (snapshot := prices.get((product.store_slug, product.mp_product_id or "")))
                    else product.price,
                    "calculator": drr.calculator(
                        today,
                        turnover_data,
                        current_sources,
                        store=product.store_slug,
                        article=product.article,
                        buyout=cabinet.buyout,
                        advertising_week=drr.calculator_advertising_week(
                            today,
                            turnover_data,
                            week_sources,
                            store=product.store_slug,
                            article=product.article,
                            credentials_present=configured[product.store_slug],
                        ),
                    ),
                    "advertising": product.advertising.model_copy(
                        update={
                            "traffic_coverage": (
                                traffic := traffic_metrics(period, product_sources, article=product.article)
                            ).coverage,
                            "click_spend": traffic.click_spend,
                            "impressions": traffic.impressions,
                            "clicks": traffic.clicks,
                            "ctr": traffic.ctr,
                            "cpc": traffic.cpc,
                            "spend_summary": (
                                summary := spend_summary(
                                    period,
                                    product_sources,
                                    article=product.article,
                                    credentials_present=configured[product.store_slug],
                                )
                            ),
                            "spend": float(summary.amount) if summary.amount is not None else None,
                            "coverage": OzonCoverage(
                                dates=tuple(
                                    day.day.isoformat() for day in summary.days if day.amount is not None
                                ),
                                days=sum(day.amount is not None for day in summary.days),
                                expected_days=len(summary.days),
                                complete=summary.complete and not summary.stale,
                                missing_dates=tuple(day.isoformat() for day in summary.missing_dates),
                                partial_dates=tuple(day.isoformat() for day in summary.partial_dates),
                                stale=summary.stale,
                            ),
                            "breakdown": tuple(
                                breakdown(
                                    kind,
                                    period,
                                    product_sources,
                                    article=product.article,
                                    credentials_present=configured[product.store_slug],
                                )
                                for kind in AdvertisingKind
                            ),
                            "drr_basis": (
                                basis := drr.metrics(
                                    period,
                                    turnover_data,
                                    product_sources,
                                    store=product.store_slug,
                                    article=product.article,
                                    spending=summary,
                                    buyout=cabinet.buyout,
                                )
                            ),
                            "drr": basis.drr,
                            "drr_coverage": basis.coverage,
                        }
                    ),
                    "data_issues": tuple(
                        issue
                        for issue in product.data_issues
                        if issue.code != OzonDataIssueCode.ADVERTISING_UNAVAILABLE
                        and not (
                            issue.code == OzonDataIssueCode.SOURCE_ERROR and issue.source == "advertising"
                        )
                    )
                    + (
                        (OzonDataIssue(code=OzonDataIssueCode.ADVERTISING_UNAVAILABLE),)
                        if summary.amount is None
                        else ()
                    )
                    + tuple(
                        OzonDataIssue(code=OzonDataIssueCode.SOURCE_ERROR, source="advertising", detail=error)
                        for error in sorted(
                            {status.error for status in (*product_sources, *current_sources) if status.error}
                        )
                    ),
                }
            )
            for product in result.products
            for product_sources in (advertising_indexes[product.store_slug].article(product.article),)
            for current_sources in (today_indexes[product.store_slug].article(product.article),)
            for week_sources in (week_indexes[product.store_slug].article(product.article),)
        )
        unallocated = tuple(
            StoreAdvertisingBreakdown(
                store_slug=store,
                period=period,
                over_expenses=sum(
                    (adjustment.over_expenses for adjustment in adjustments if adjustment.period == period),
                    Decimal(0),
                ),
                unallocated=tuple(
                    breakdown(
                        kind,
                        period,
                        by_store[store],
                        article=None,
                        credentials_present=configured[store],
                    )
                    for kind in AdvertisingKind
                ),
                corrections=tuple(
                    correction
                    for adjustment in adjustments
                    if adjustment.period == period
                    for correction in adjustment.corrections
                ),
                adjustments=adjustments,
            )
            for store in query.stores
            for adjustments in (window_adjustments(by_store[store]),)
        )
        history = {
            (value.store_slug, value.article, value.day): value
            for value in await OzonMarginRepository(self.database).history(
                query.stores, query.period_from, query.period_to
            )
        }
        enriched = []
        for product in products:
            assert product.cabinet is not None
            saved = ozon_margin.snapshot(
                OzonMarginSource(
                    store_slug=product.store_slug,
                    article=product.article,
                    mp_product_id=product.mp_product_id,
                    purchase_price=Decimal(str(product.details.purchase_cost))
                    if product.details.purchase_cost is not None
                    else None,
                    fulfillment_cost=Decimal(str(product.details.fulfillment_cost))
                    if product.details.fulfillment_cost is not None
                    else None,
                    team_commission_percent=Decimal(str(product.details.team_commission_percent))
                    if product.details.team_commission_percent is not None
                    else None,
                ),
                product.price,
                product.commission,
                product.logistics,
                product.storage,
                product.cabinet,
                datetime.now(UTC),
            )
            current_ads = spend_summary(
                AdvertisingPeriod(start=today, end=today),
                today_indexes[product.store_slug].article(product.article),
                article=product.article,
                credentials_present=configured[product.store_slug],
            )
            daily = (
                tuple(
                    ozon_margin.calculate_day(
                        product.store_slug,
                        product.article,
                        value.day,
                        history.get((product.store_slug, product.article, value.day)),
                        turnover_data,
                        value,
                    )
                    for value in product.advertising.spend_summary.days
                )
                if product.advertising.spend_summary
                else ()
            )
            enriched.append(
                product.model_copy(
                    update={
                        "current_economics": ozon_margin.current(
                            ozon_margin.calculate_day(
                                product.store_slug,
                                product.article,
                                today,
                                saved,
                                turnover_data,
                                current_ads.days[0],
                            ),
                            period_turnover(
                                turnover_data,
                                product.store_slug,
                                product.article,
                                today,
                                today,
                                include_open_days=True,
                            ),
                        ),
                        "economics_7d": ozon_margin.period(
                            product.economics_7d, daily, query.period_from, query.period_to
                        ),
                    }
                )
            )
        return result.model_copy(update={"products": tuple(enriched), "advertising_unallocated": unallocated})
