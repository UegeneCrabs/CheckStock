from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.core.constants import (
    OZON_ANALYTICS,
    OZON_CABINET,
    OZON_MARGIN,
    TIME,
    Marketplace,
    OzonAnalyticsReason,
    OzonPriceField,
    OzonPriceState,
)
from app.core.stores import STORES
from app.dto.orders import OzonArticleIndex
from app.dto.ozon_advertising import AdvertisingPeriod
from app.dto.ozon_analytics import (
    OzonAnalyticsAnalyzer,
    OzonAnalyticsApiError,
    OzonAnalyticsApiRow,
    OzonAnalyticsCatalog,
    OzonAnalyticsComment,
    OzonAnalyticsCoverage,
    OzonAnalyticsEphemerides,
    OzonAnalyticsEphemeridesRow,
    OzonAnalyticsFunnelPage,
    OzonAnalyticsFunnelValue,
    OzonAnalyticsHistory,
    OzonAnalyticsIssue,
    OzonAnalyticsMetricQuality,
    OzonAnalyticsRow,
    OzonAnalyticsSales,
    OzonAnalyticsSalesRow,
    OzonAnalyticsSalesSources,
    OzonAnalyticsSourceBundle,
    OzonAnalyticsTurnover,
    OzonAnalyticsWeekInfo,
    OzonAnalyticsWeekMetrics,
    OzonAnalyticsWeights,
)
from app.dto.ozon_economics import OzonOrderDay
from app.dto.ozon_margin import OzonMarginDay
from app.dto.ozon_prices import PriceValue
from app.economics.ozon_margin import calculate_day
from app.ozon.advertising_normalizer import source_days, spend_summary, traffic_metrics
from app.ozon.advertising_read_index import AdvertisingReadIndex


def dates(start: date, end: date) -> tuple[date, ...]:
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def monday(value: date) -> date:
    return value - timedelta(days=value.weekday())


def total(values: Iterable[float | int | Decimal | None]) -> float | None:
    known = tuple(float(value) for value in values if value is not None)
    return sum(known) if known else None


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    return (
        round(numerator / denominator * OZON_ANALYTICS.percent_factor, OZON_ANALYTICS.money_precision)
        if numerator is not None and denominator is not None and denominator > 0
        else None
    )


def drr_ratio(numerator: float | None, denominator: float | None) -> float | None:
    return 0.0 if numerator == 0 and denominator == 0 else ratio(numerator, denominator)


def metric_quality(
    *,
    complete: bool,
    known: bool,
    stale: bool = False,
    expected_dates: tuple[date, ...] = (),
    known_dates: tuple[date, ...] = (),
) -> OzonAnalyticsMetricQuality:
    reasons: tuple[OzonAnalyticsReason, ...] = (
        (OzonAnalyticsReason.MISSING_SOURCE,)
        if not known
        else (OzonAnalyticsReason.PARTIAL,)
        if not complete
        else ()
    )
    if stale:
        reasons += (OzonAnalyticsReason.STALE,)
    return OzonAnalyticsMetricQuality(
        complete=complete,
        stale=stale,
        reasons=reasons,
        expected_dates=expected_dates,
        known_dates=known_dates,
    )


def margin_quality(value: OzonMarginDay | None, *, stale: bool = False) -> OzonAnalyticsMetricQuality:
    if value is None:
        return metric_quality(complete=False, known=False, stale=stale)
    stale_issues = {OZON_MARGIN.sources_stale, OZON_MARGIN.orders_partial}
    if value.advertising_spend is not None:
        stale_issues.add(OZON_MARGIN.advertising_partial)
    return metric_quality(
        complete=not value.missing and not (set(value.issues) - stale_issues),
        known=value.profit is not None and value.purchase_value is not None,
        stale=stale or bool(set(value.issues) & stale_issues),
        expected_dates=(value.day,),
        known_dates=(value.day,) if value.profit is not None and value.purchase_value is not None else (),
    )


def week_info(value: date) -> OzonAnalyticsWeekInfo:
    year, number, _ = value.isocalendar()
    return OzonAnalyticsWeekInfo(
        start=value, end=value + timedelta(days=OZON_ANALYTICS.week_days - 1), year=year, number=number
    )


def comments_for(
    comments: tuple[OzonAnalyticsComment, ...], store: str, article: str
) -> dict[str, OzonAnalyticsComment]:
    return {
        item.week.isoformat() + ":" + item.kind: item
        for item in comments
        if item.store_slug == store and item.article == article
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonAnalyticsPriceSelection:
    amount: float | None = None
    day: date | None = None
    reason: OzonAnalyticsReason | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonAnalyticsDrrDay:
    day: date
    spend: float
    bought_amount: float
    complete: bool
    stale: bool


def price_amount(value: PriceValue) -> float | None:
    return (
        float(value.amount) if value.amount is not None and value.state == OzonPriceState.AVAILABLE else None
    )


class OzonAnalyticsCalculator:
    def __init__(self, sources: OzonAnalyticsSourceBundle, now: datetime):
        self.sources = sources
        self.now = now.astimezone(TIME.moscow_timezone)
        self.today = self.now.date()
        self.orders = {
            (store, article, item.day): item
            for (store, article), days in sources.turnover.products.items()
            for item in days
        }
        self.current = {(item.store_slug, item.article): item for item in sources.current}
        self.buyouts = {(item.store_slug, item.day): item for item in sources.buyouts}
        self.margins = {(item.store_slug, item.article, item.day): item for item in sources.margins}
        self.cabinets = {item.store_slug: item.values for item in sources.cabinets}
        self.funnel = {(item.store_slug, item.source_day): item for item in sources.funnel}
        self.statuses = {(item.store_slug, item.source, item.source_day): item for item in sources.statuses}

    def weekly_price(
        self, product: OzonAnalyticsCatalog, field: OzonPriceField, start: date, end: date
    ) -> OzonAnalyticsPriceSelection:
        candidates = tuple(
            value
            for item in self.sources.prices
            for value in (item.values.value(field),)
            if item.store_slug == product.store_slug
            and item.article == product.article
            and (product.mp_product_id is None or item.product_id == product.mp_product_id)
            and start <= item.day <= end
            and value.received_at is not None
            and value.received_at.astimezone(TIME.moscow_timezone).date() == item.day
            and value.state == OzonPriceState.AVAILABLE
            and value.amount is not None
        )
        if not candidates:
            return OzonAnalyticsPriceSelection(reason=OzonAnalyticsReason.MISSING_SOURCE)
        latest = max(value.received_at for value in candidates if value.received_at is not None)
        newest = tuple(value for value in candidates if value.received_at == latest)
        if (
            len({(value.amount, value.currency) for value in newest}) != 1
            or product.mp_product_id is None
            and len(
                {
                    item.product_id
                    for item in self.sources.prices
                    if item.store_slug == product.store_slug
                    and item.article == product.article
                    and start <= item.day <= end
                }
            )
            > 1
        ):
            return OzonAnalyticsPriceSelection(reason=OzonAnalyticsReason.MAPPING_CONFLICT)
        return OzonAnalyticsPriceSelection(
            amount=price_amount(newest[0]), day=latest.astimezone(TIME.moscow_timezone).date()
        )

    def order(self, store: str, article: str, day: date) -> OzonOrderDay | None:
        if day > self.today:
            return None
        value = self.orders.get((store, article, day))
        if value is not None:
            return value
        known = self.sources.turnover.eligible_days.get(
            store, frozenset()
        ) | self.sources.turnover.confirmed_days.get(store, frozenset())
        if day in known:
            return OzonOrderDay(
                store_slug=store,
                article=article,
                day=day,
                amount=Decimal(0),
                gross_amount=Decimal(0),
                cancelled_amount=Decimal(0),
            )
        return None

    def gross(self, store: str, article: str, day: date) -> float | None:
        value = self.order(store, article, day)
        return float(value.ordered + value.cancelled) if value else None

    def order_complete(self, store: str, day: date) -> bool:
        return day <= self.today and day in (
            self.sources.turnover.eligible_days.get(store, frozenset())
            | self.sources.turnover.confirmed_days.get(store, frozenset())
        )

    def gross_amount(self, store: str, article: str, day: date) -> float | None:
        value = self.order(store, article, day)
        return float(value.gross_amount) if value and value.gross_amount is not None else None

    def buyout(self, store: str, article: str, day: date) -> float | None:
        snapshot = self.buyouts.get((store, day))
        if (
            snapshot is None
            or snapshot.formula_version != OZON_CABINET.buyout_formula_version
            or snapshot.period_to != day - timedelta(days=1)
            or snapshot.period_from != day - timedelta(days=OZON_CABINET.default_period_days)
        ):
            return None
        actual = next((item.percent for item in snapshot.products if item.article == article), None)
        if actual is not None and actual > 0 and not snapshot.missing_dates:
            return actual
        margin = self.margins.get((store, article, day))
        if margin is not None and margin.buyout_percent is not None:
            return float(margin.buyout_percent)
        return actual if not snapshot.missing_dates else None

    def analyzer(self, week: date) -> OzonAnalyticsAnalyzer:
        today = self.today
        days = dates(week, week + timedelta(days=OZON_ANALYTICS.week_days - 1))
        anchor = min(today, days[-1])
        traffic_days = dates(anchor - timedelta(days=OZON_ANALYTICS.traffic_days - 1), anchor)
        previous = dates(week - timedelta(days=OZON_ANALYTICS.week_days), week - timedelta(days=1))
        stock_week = monday(today) - timedelta(days=OZON_ANALYTICS.week_days)
        stock_days = dates(stock_week, stock_week + timedelta(days=OZON_ANALYTICS.week_days - 1))
        period_days = tuple(day for day in days if day <= today)
        period = AdvertisingPeriod(start=week, end=anchor)
        ad_indexes = {
            store: AdvertisingReadIndex(
                source_days(
                    AdvertisingPeriod(start=min(previous[0], traffic_days[0]), end=anchor),
                    tuple(status for status in self.sources.advertising if status.store_slug == store),
                )
            )
            for store in self.cabinets
        }
        rows = []
        for product in self.sources.catalog:
            store, article = product.store_slug, product.article
            current = self.current.get((store, article))
            product_sources = ad_indexes[store].article(article)
            summary = spend_summary(
                period,
                product_sources,
                article=article,
                credentials_present=store in self.sources.advertising_configured,
            )
            ads = {item.day: item for item in summary.days}
            traffic = traffic_metrics(period, product_sources, article=article)
            daily_traffic = tuple(
                traffic_metrics(AdvertisingPeriod(start=day, end=day), product_sources, article=article)
                for day in traffic_days
            )
            yesterday = anchor - timedelta(days=1)
            yesterday_summary = spend_summary(
                AdvertisingPeriod(start=yesterday, end=yesterday),
                product_sources,
                article=article,
                credentials_present=store in self.sources.advertising_configured,
            )
            yesterday_spend = (
                float(yesterday_summary.amount) if yesterday_summary.amount is not None else None
            )
            drr_pairs = tuple(
                OzonAnalyticsDrrDay(
                    day=day,
                    spend=float(ad.amount),
                    bought_amount=amount * buyout / OZON_ANALYTICS.percent_factor,
                    complete=ad.complete and self.order_complete(store, day),
                    stale=ad.stale or store in self.sources.turnover.stale_stores,
                )
                for day in period_days
                if (ad := ads.get(day)) is not None
                and ad.amount is not None
                and (amount := self.gross_amount(store, article, day)) is not None
                and (buyout := self.buyout(store, article, day)) is not None
            )
            spend = total(pair.spend for pair in drr_pairs)
            bought = total(pair.bought_amount for pair in drr_pairs)
            drr_quality = metric_quality(
                complete=len(drr_pairs) == len(period_days) and all(pair.complete for pair in drr_pairs),
                known=bool(drr_pairs),
                stale=summary.stale or store in self.sources.turnover.stale_stores,
                expected_dates=period_days,
                known_dates=tuple(pair.day for pair in drr_pairs),
            )
            yesterday_amount = self.gross_amount(store, article, yesterday)
            yesterday_buyout = self.buyout(store, article, yesterday)
            yesterday_basis = (
                yesterday_amount * yesterday_buyout / OZON_ANALYTICS.percent_factor
                if yesterday_amount is not None and yesterday_buyout is not None
                else None
            )
            yesterday_quality = metric_quality(
                complete=yesterday_spend is not None
                and yesterday_basis is not None
                and yesterday_summary.complete
                and self.order_complete(store, yesterday),
                known=yesterday_spend is not None and yesterday_basis is not None,
                stale=yesterday_summary.stale or store in self.sources.turnover.stale_stores,
                expected_dates=(yesterday,),
                known_dates=(yesterday,)
                if yesterday_spend is not None and yesterday_basis is not None
                else (),
            )
            previous_pairs = tuple(
                (count, amount)
                for day in previous
                if (count := self.gross(store, article, day)) is not None
                and (amount := self.gross_amount(store, article, day)) is not None
            )
            previous_count = total(pair[0] for pair in previous_pairs)
            previous_amount = total(pair[1] for pair in previous_pairs)
            previous_price = (
                round(previous_amount / previous_count, OZON_ANALYTICS.money_precision)
                if previous_amount is not None and previous_count is not None and previous_count > 0
                else None
            )
            previous_quality = metric_quality(
                complete=len(previous_pairs) == len(previous)
                and all(self.order_complete(store, day) for day in previous),
                known=previous_price is not None,
                stale=store in self.sources.turnover.stale_stores,
                expected_dates=previous,
                known_dates=tuple(
                    day
                    for day in previous
                    if self.gross(store, article, day) is not None
                    and self.gross_amount(store, article, day) is not None
                ),
            )
            stock_orders = tuple(self.gross(store, article, day) for day in stock_days)
            stock_complete = all(
                value is not None and self.order_complete(store, day)
                for day, value in zip(stock_days, stock_orders, strict=True)
            )
            stock_orders_total = total(stock_orders)
            stock_daily = (
                stock_orders_total / OZON_ANALYTICS.week_days
                if stock_complete and stock_orders_total is not None
                else None
            )
            stock_values = (current.stock.fbo, current.stock.fbs) if current else (None, None)
            stock = total(stock_values)
            stock_partial = any(value is None for value in stock_values)
            coefficient = self.cabinets[store].forecast.coefficient_at_hour(self.now.hour)
            goal = product.goal_week
            day_goal = (
                product.goal_day
                if product.goal_day is not None
                else goal / OZON_ANALYTICS.week_days
                if goal is not None
                else None
            )
            plan = (
                round(day_goal * previous_price, OZON_ANALYTICS.money_precision)
                if day_goal is not None and previous_price is not None
                else None
            )
            fact = self.gross_amount(store, article, today)
            forecast = (
                round(fact * coefficient, OZON_ANALYTICS.money_precision)
                if fact is not None and coefficient is not None
                else None
            )
            difference = (
                round(forecast - plan, OZON_ANALYTICS.money_precision)
                if forecast is not None and plan is not None
                else None
            )
            daily_margins = tuple(
                current.current_economics.calculation
                if day == today and current and current.current_economics.calculation
                else calculate_day(
                    store,
                    article,
                    day,
                    self.margins.get((store, article, day)),
                    self.sources.turnover,
                    ads.get(day),
                )
                for day in period_days
            )
            roi_pairs = tuple(
                (value.profit, value.purchase_value)
                for value in daily_margins
                if value.profit is not None and value.purchase_value is not None
            )
            profit = total(pair[0] for pair in roi_pairs)
            purchase = total(pair[1] for pair in roi_pairs)
            roi_days_quality = tuple(
                margin_quality(
                    value,
                    stale=store in self.sources.turnover.stale_stores
                    or bool((day_ads := ads.get(value.day)) and day_ads.stale),
                )
                for value in daily_margins
            )
            roi_quality = metric_quality(
                complete=len(roi_pairs) == len(period_days)
                and all(value.complete for value in roi_days_quality),
                known=ratio(profit, purchase) is not None,
                stale=any(value.stale for value in roi_days_quality),
                expected_dates=period_days,
                known_dates=tuple(day for value in roi_days_quality for day in value.known_dates),
            )
            current_roi_quality = margin_quality(
                current.current_economics.calculation if current else None,
                stale=store in self.sources.turnover.stale_stores
                or bool(
                    current and current.advertising.spend_summary and current.advertising.spend_summary.stale
                ),
            )
            traffic_quality = metric_quality(
                complete=not traffic.coverage.missing_dates and not traffic.coverage.partial_dates,
                known=traffic.ctr is not None,
                stale=traffic.coverage.stale,
                expected_dates=period_days,
                known_dates=tuple(date.fromisoformat(day) for day in traffic.coverage.dates),
            )
            issues: list[OzonAnalyticsIssue] = []
            if stock is not None and stock_partial:
                issues.extend(
                    OzonAnalyticsIssue(field=field, reason=OzonAnalyticsReason.PARTIAL)
                    for field in ("stock", "stockDays")
                )
            if not stock_complete:
                issues.append(OzonAnalyticsIssue(field="stockDays", reason=OzonAnalyticsReason.PARTIAL))
            for field, quality in (
                ("drr", drr_quality),
                ("yesterdayDrr", yesterday_quality),
                ("roi", roi_quality),
                ("currentRoi", current_roi_quality),
                ("totalCtr", traffic_quality),
                ("previousPrice", previous_quality),
            ):
                issues.extend(OzonAnalyticsIssue(field=field, reason=reason) for reason in quality.reasons)
            if coefficient is None:
                issues.extend(
                    OzonAnalyticsIssue(field=field, reason=OzonAnalyticsReason.NOT_CONFIGURED)
                    for field in ("coeff", "forecast", "difference", "deviation")
                )
            cart_values: list[int | None] = []
            provisional: list[bool] = []
            for idx, day in enumerate(traffic_days):
                snapshot = self.funnel.get((store, day))
                entries = (
                    tuple(value for value in snapshot.products if value.article == article)
                    if snapshot
                    else ()
                )
                value = (
                    sum(value.carts for value in entries)
                    if entries and day <= self.now.astimezone(UTC).date()
                    else None
                )
                if value == 0 and snapshot and not snapshot.mapping_complete:
                    value = None
                cart_values.append(value)
                provisional.append(day == self.now.astimezone(UTC).date())
                day_status = self.statuses.get((store, OZON_ANALYTICS.funnel_source, day))
                status = day_status or (
                    self.statuses.get((store, OZON_ANALYTICS.funnel_source, None)) if value is None else None
                )
                reason = (
                    status.reason
                    if status and status.reason
                    else OzonAnalyticsReason.MISSING_SOURCE
                    if value is None
                    else OzonAnalyticsReason.PARTIAL
                    if snapshot and not snapshot.mapping_complete
                    else OzonAnalyticsReason.PROVISIONAL
                    if provisional[-1]
                    else OzonAnalyticsReason.OBSERVED
                )
                if (
                    snapshot
                    and day_status
                    and day_status.reason
                    and day_status.attempted_at > snapshot.observed_at
                ):
                    reason = OzonAnalyticsReason.STALE
                issues.append(
                    OzonAnalyticsIssue(
                        field="carts" + str(idx),
                        day=day,
                        reason=reason,
                        observed_at=snapshot.observed_at if snapshot else None,
                        next_retry_at=status.next_retry_at if status else None,
                    )
                )
            if store in self.sources.turnover.stale_stores:
                issues.append(OzonAnalyticsIssue(field="orders", reason=OzonAnalyticsReason.STALE))
            known_orders = sum(self.gross(store, article, day) is not None for day in period_days)
            for field, missing in (
                ("stock", stock is None),
                ("stockDays", stock_daily is None or stock_daily <= 0),
                ("drr", drr_ratio(spend, bought) is None),
                ("roi", ratio(profit, purchase) is None),
                ("currentRoi", current is None or current.current_economics.roi is None),
                ("previousPrice", previous_price is None),
                ("rating", current is None or current.rating is None),
                ("price", current is None or current.price.seller_price.amount is None),
            ):
                if missing:
                    issues.append(OzonAnalyticsIssue(field=field, reason=OzonAnalyticsReason.MISSING_SOURCE))
            notes = tuple(
                item for item in self.sources.notes if item.store_slug == store and item.article == article
            )
            seller = price_amount(current.price.seller_price) if current else None
            black = price_amount(current.price.black_price) if current else None
            current_economics = current.current_economics if current else None
            rows.append(
                OzonAnalyticsRow(
                    id=store + ":" + article,
                    store_slug=store,
                    marketplace=Marketplace.OZON,
                    metric_key=article,
                    project=STORES[store].name,
                    article=article,
                    barcode=product.barcode,
                    barcodes=(product.barcode,) if product.barcode else (),
                    name=product.name or article,
                    image=product.image,
                    manager=product.manager,
                    category=product.category,
                    code=product.code,
                    is_new=current.is_new if current else None,
                    stock=stock,
                    stockPartial=stock_partial,
                    stockDays=round(stock / stock_daily, OZON_ANALYTICS.stock_days_precision)
                    if stock is not None and stock_daily is not None and stock_daily > 0
                    else None,
                    stockDailyOrders=stock_daily,
                    stockOrdersComplete=stock_complete,
                    stockStatus=product.stock_status,
                    stockEnds=product.stock_end_week,
                    roi=ratio(profit, purchase),
                    currentRoi=current_economics.roi if current_economics else None,
                    drr=drr_ratio(spend, bought),
                    previousPrice=previous_price,
                    impressions=tuple(
                        float(value.impressions) if value.impressions is not None else None
                        for value in daily_traffic
                    ),
                    clicks=tuple(
                        float(value.clicks) if value.clicks is not None else None for value in daily_traffic
                    ),
                    ctr=tuple(value.ctr for value in daily_traffic),
                    carts=tuple(cart_values),
                    spend=yesterday_spend,
                    yesterdayDrr=drr_ratio(yesterday_spend, yesterday_basis),
                    totalCtr=traffic.ctr,
                    rating=current.rating if current else None,
                    week=tuple(self.gross(store, article, day) for day in days),
                    goal=goal,
                    dayGoal=day_goal,
                    spp=ratio(seller - black, seller) if seller is not None and black is not None else None,
                    wallet=price_amount(current.price.green_price) if current else None,
                    price=seller,
                    coeff=coefficient,
                    fact=fact,
                    forecast=forecast,
                    difference=difference,
                    deviation=ratio(difference, plan),
                    plan=plan,
                    notes=tuple(tuple(note for note in notes if note.action_date == day) for day in days),
                    noteHistory=notes,
                    updated={
                        "reference": product.source_synced_at,
                        "stock": product.stock_updated_at,
                        "prices": current.price.seller_price.received_at if current else None,
                        "orders": max(
                            (
                                value
                                for (slug, _), value in self.sources.turnover.updated_at.items()
                                if slug == store
                            ),
                            default=None,
                        ),
                        "advertising": max(
                            (
                                item.attempted_at
                                for item in self.sources.advertising
                                if item.store_slug == store
                            ),
                            default=None,
                        ),
                    },
                    coverage=OzonAnalyticsCoverage(
                        orders=known_orders,
                        advertising=sum(value.complete for value in summary.days),
                        expected=len(period_days),
                        drr=sum(pair.complete for pair in drr_pairs),
                        previousPrice=len(previous_pairs),
                        traffic=traffic.coverage.days,
                        trafficComplete=traffic.coverage.complete,
                        roiComplete=len(roi_pairs) == len(period_days)
                        and all(not value.missing and not value.issues for value in daily_margins),
                        currentRoiComplete=current_economics.complete if current_economics else False,
                        roiMessages=tuple(
                            dict.fromkeys(
                                message
                                for value in daily_margins
                                for message in (*value.missing, *value.issues)
                            )
                        ),
                        stale=store in self.sources.turnover.stale_stores or summary.stale,
                        partial_dates=tuple(
                            day
                            for day in period_days
                            if self.order(store, article, day) is not None
                            and day not in self.sources.turnover.eligible_days.get(store, frozenset())
                            and day not in self.sources.turnover.confirmed_days.get(store, frozenset())
                        ),
                    ),
                    weights=OzonAnalyticsWeights(
                        currentProfit=current_economics.margin if current_economics else None,
                        currentPurchase=float(current_economics.purchase_value)
                        if current_economics and current_economics.purchase_value is not None
                        else None,
                        spend=spend,
                        boughtAmount=bought,
                        yesterdaySpend=yesterday_spend if yesterday_basis is not None else None,
                        yesterdayAmount=yesterday_basis if yesterday_spend is not None else None,
                        impressions=float(traffic.impressions) if traffic.impressions is not None else None,
                        clicks=float(traffic.clicks) if traffic.clicks is not None else None,
                        profit=profit,
                        purchase=purchase,
                        previousCount=previous_count,
                        previousAmount=previous_amount,
                    ),
                    field_issues=tuple(issues),
                    quality={
                        "drr": drr_quality,
                        "yesterdayDrr": yesterday_quality,
                        "roi": roi_quality,
                        "currentRoi": current_roi_quality,
                        "totalCtr": traffic_quality,
                        "previousPrice": previous_quality,
                        "spend": metric_quality(
                            complete=yesterday_summary.complete,
                            known=yesterday_spend is not None,
                            stale=yesterday_summary.stale,
                            expected_dates=(yesterday,),
                            known_dates=(yesterday,) if yesterday_spend is not None else (),
                        ),
                        "stock": metric_quality(complete=not stock_partial, known=stock is not None),
                        "stockDays": metric_quality(
                            complete=stock_complete and not stock_partial and bool(stock_daily),
                            known=stock is not None and stock_daily is not None and stock_daily > 0,
                            stale=store in self.sources.turnover.stale_stores,
                            expected_dates=stock_days,
                            known_dates=tuple(
                                day
                                for day, value in zip(stock_days, stock_orders, strict=True)
                                if value is not None
                            ),
                        ),
                        **{
                            field + str(index): metric_quality(
                                complete=not value.coverage.missing_dates
                                and not value.coverage.partial_dates,
                                known=(value.ctr if field == "ctr" else value.impressions) is not None,
                                stale=value.coverage.stale,
                                expected_dates=(day,),
                                known_dates=tuple(
                                    date.fromisoformat(known) for known in value.coverage.dates
                                ),
                            )
                            for index, (day, value) in enumerate(
                                zip(traffic_days, daily_traffic, strict=True)
                            )
                            for field in ("ctr", "impressions")
                        },
                        **{
                            "day" + str(index): metric_quality(
                                complete=self.order_complete(store, day),
                                known=self.gross(store, article, day) is not None,
                                stale=store in self.sources.turnover.stale_stores,
                                expected_dates=(day,),
                                known_dates=(day,) if self.gross(store, article, day) is not None else (),
                            )
                            for index, day in enumerate(days)
                        },
                    },
                    carts_source_days=traffic_days,
                    carts_provisional=tuple(provisional),
                )
            )
        explained_rows = []
        for row in rows:
            fields = {item.field for item in row.field_issues}
            missing_issues = tuple(
                OzonAnalyticsIssue(field=field, reason=OzonAnalyticsReason.MISSING_SOURCE)
                for field in OZON_ANALYTICS.scalar_metric_fields
                if getattr(row, field) is None and field not in fields
            )
            missing_issues += tuple(
                OzonAnalyticsIssue(
                    field=("day" if field == "week" else field) + str(index),
                    reason=OzonAnalyticsReason.MISSING_SOURCE,
                    day=(row.carts_source_days[index] if field == "carts" else traffic_days[index])
                    if field != "week"
                    else days[index],
                )
                for field in OZON_ANALYTICS.indexed_metric_fields
                for index, value in enumerate(getattr(row, field))
                if value is None and ("day" if field == "week" else field) + str(index) not in fields
            )
            partial = tuple(
                OzonAnalyticsIssue(field=field, reason=OzonAnalyticsReason.PARTIAL)
                for field, known in (
                    ("drr", row.coverage.drr),
                    ("previousPrice", int(row.quality["previousPrice"].complete) * len(previous)),
                    ("roi", int(row.quality["roi"].complete) * len(period_days)),
                )
                if getattr(row, field) is not None
                and known < (OZON_ANALYTICS.week_days if field == "previousPrice" else len(period_days))
            )
            partial += tuple(
                OzonAnalyticsIssue(field="day" + str(index), day=day, reason=OzonAnalyticsReason.PARTIAL)
                for index, day in enumerate(days)
                if day in row.coverage.partial_dates and row.week[index] is not None
            )
            explained_rows.append(
                row.model_copy(update={"field_issues": row.field_issues + missing_issues + partial})
            )
        rows = explained_rows
        rows.sort(
            key=lambda row: (
                row.fact is None,
                -(row.fact or 0),
                row.project,
                row.name.casefold(),
                row.article,
            )
        )
        return OzonAnalyticsAnalyzer(
            rows=tuple(rows),
            week=days,
            traffic_days=traffic_days,
            today=today,
            anchor=anchor,
            period_from=week,
            period_to=anchor,
            previous_from=previous[0],
            previous_to=previous[-1],
            stockPreviousFrom=stock_days[0],
            stockPreviousTo=stock_days[-1],
            turnover_day=today,
            coefficient_hour=self.now.hour,
            coefficient_valid_until=self.now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1),
            loaded_at=self.now.astimezone(UTC),
        )

    def ephemerides(self, week: date) -> OzonAnalyticsEphemerides:
        previous = week - timedelta(days=OZON_ANALYTICS.week_days)
        previous_data, current_data = self.analyzer(previous), self.analyzer(week)
        comparisons = (
            {item.id: item for item in previous_data.rows},
            {item.id: item for item in current_data.rows},
        )
        snapshots = {(item.store_slug, item.week): item for item in self.sources.stocks}
        rows = []
        for product in self.sources.catalog:
            key = product.store_slug, product.article
            row_id = product.store_slug + ":" + product.article
            snapshot = snapshots.get((product.store_slug, week))
            stock = (
                next((item for item in snapshot.products if item.article == product.article), None)
                if snapshot
                else None
            )
            metrics = []
            weekly_issues: list[OzonAnalyticsIssue] = []
            for name, start, comparison in zip(
                ("previous", "current"), (previous, week), comparisons, strict=True
            ):
                source = comparison[row_id]
                end = min(self.today, start + timedelta(days=OZON_ANALYTICS.week_days - 1))
                seller_selection = self.weekly_price(product, OzonPriceField.SELLER, start, end)
                black_selection = self.weekly_price(product, OzonPriceField.BLACK, start, end)
                wallet_selection = self.weekly_price(product, OzonPriceField.GREEN, start, end)
                seller, black = seller_selection.amount, black_selection.amount
                weekly_issues.extend(
                    OzonAnalyticsIssue(field=name + "." + field, reason=selection.reason)
                    for field, selection in (
                        ("price", seller_selection),
                        ("spp", black_selection),
                        ("wallet", wallet_selection),
                    )
                    if selection.reason is not None
                )
                references = tuple(
                    item
                    for item in self.sources.references
                    if (item.store_slug, item.article) == key and start <= item.day <= end
                )
                reference = max(references, key=lambda item: item.day) if references else None
                goal = (
                    product.goal_week
                    if start == monday(self.today)
                    else reference.goal_week
                    if reference
                    else None
                )
                completed = start + timedelta(days=OZON_ANALYTICS.week_days - 1) < self.today
                facts = tuple(
                    self.order(product.store_slug, product.article, day) for day in dates(start, end)
                )
                fact = total(
                    value.ordered if completed else value.ordered + value.cancelled
                    for value in facts
                    if value is not None
                )
                metrics.append(
                    OzonAnalyticsWeekMetrics(
                        spp=ratio(seller - black, seller)
                        if seller is not None and black is not None
                        else None,
                        wallet=wallet_selection.amount,
                        price=seller,
                        priceDay=seller_selection.day,
                        goal=goal,
                        fact=fact,
                        factNet=completed,
                        turnover=goal * seller if goal is not None and seller is not None else None,
                        impressions=source.weights.impressions,
                        ctr=source.totalCtr,
                        roi=source.roi,
                        drr=source.drr,
                        weights=source.weights,
                        coverage=source.coverage,
                        quality={
                            "drr": source.quality["drr"],
                            "roi": source.quality["roi"],
                            "ctr": source.quality["totalCtr"],
                            "fact": metric_quality(
                                complete=not source.coverage.partial_dates
                                and source.coverage.orders == source.coverage.expected,
                                known=fact is not None,
                                stale=product.store_slug in self.sources.turnover.stale_stores,
                                expected_dates=dates(start, end),
                                known_dates=tuple(
                                    day
                                    for day in dates(start, end)
                                    if self.order(product.store_slug, product.article, day) is not None
                                ),
                            ),
                            "impressions": metric_quality(
                                complete=source.quality["totalCtr"].complete,
                                known=source.weights.impressions is not None,
                                stale=source.quality["totalCtr"].stale,
                                expected_dates=dates(start, end),
                                known_dates=source.quality["totalCtr"].known_dates,
                            ),
                        },
                    )
                )
                for issue in source.field_issues:
                    weekly_field = {"totalCtr": "ctr", "roi": "roi", "drr": "drr"}.get(issue.field)
                    if weekly_field is not None:
                        weekly_issues.append(issue.model_copy(update={"field": name + "." + weekly_field}))
                for field in (
                    "spp",
                    "wallet",
                    "price",
                    "goal",
                    "fact",
                    "turnover",
                    "impressions",
                    "ctr",
                    "roi",
                    "drr",
                ):
                    metric_value: object = getattr(metrics[-1], field)
                    issue_field = name + "." + field
                    if metric_value is None and not any(
                        issue.field == issue_field for issue in weekly_issues
                    ):
                        weekly_issues.append(
                            OzonAnalyticsIssue(field=issue_field, reason=OzonAnalyticsReason.MISSING_SOURCE)
                        )
                    elif metric_value is not None and (
                        (field in ("ctr", "impressions") and not source.quality["totalCtr"].complete)
                        or (
                            field == "fact"
                            and (
                                source.coverage.orders < source.coverage.expected
                                or source.coverage.partial_dates
                            )
                        )
                    ):
                        weekly_issues.append(
                            OzonAnalyticsIssue(field=issue_field, reason=OzonAnalyticsReason.PARTIAL)
                        )
            current = comparisons[1][row_id]
            components = (
                (stock.fbs, stock.fbo, stock.ff, stock.from_customer) if stock else (None, None, None, None)
            )
            rows.append(
                OzonAnalyticsEphemeridesRow(
                    **product.model_dump(),
                    id=row_id,
                    marketplace=Marketplace.OZON,
                    metric_key=product.article,
                    project=STORES[product.store_slug].name,
                    stock=total(components),
                    stockPartial=any(value is None for value in components),
                    stockWeek=week,
                    stockUpdated=snapshot.captured_at if snapshot else None,
                    transitUpdated=snapshot.captured_at if snapshot else None,
                    fbs=stock.fbs if stock else None,
                    fbo=stock.fbo if stock else None,
                    ff=stock.ff if stock else None,
                    fromCustomer=stock.from_customer if stock else None,
                    stockStatus=product.stock_status,
                    stockEnds=product.stock_end_week,
                    previous=metrics[0],
                    current=metrics[1],
                    rating=current.rating,
                    is_new=current.is_new,
                    dayTurnover=OzonAnalyticsTurnover(
                        plan=current.plan,
                        fact=current.fact,
                        forecast=current.forecast,
                        difference=current.difference,
                        deviation=current.deviation,
                    ),
                    comments=comments_for(self.sources.comments, *key),
                    quality={
                        "stock": metric_quality(
                            complete=all(value is not None for value in components),
                            known=any(value is not None for value in components),
                        )
                    },
                    field_issues=current.field_issues
                    + tuple(weekly_issues)
                    + (stock.field_issues if stock else ())
                    + (
                        (OzonAnalyticsIssue(field="stock", reason=OzonAnalyticsReason.MISSING_SOURCE),)
                        if stock is None
                        else ()
                    ),
                )
            )
        rows.sort(
            key=lambda row: (
                row.dayTurnover.fact is None,
                -(row.dayTurnover.fact or 0),
                row.project,
                row.name.casefold(),
                row.article,
            )
        )
        return OzonAnalyticsEphemerides(
            rows=tuple(rows),
            current=week_info(week),
            previous=week_info(previous),
            weeks=tuple(
                week_info(week - timedelta(weeks=offset)) for offset in range(OZON_ANALYTICS.decision_weeks)
            ),
            today=self.today,
            through=current_data.anchor,
            coefficient_hour=self.now.hour,
            coefficient_valid_until=current_data.coefficient_valid_until,
            loaded_at=self.now.astimezone(UTC),
        )


def history(
    catalog: tuple[OzonAnalyticsCatalog, ...], comments: tuple[OzonAnalyticsComment, ...], week: date
) -> OzonAnalyticsHistory:
    rows = tuple(
        OzonAnalyticsEphemeridesRow(
            **product.model_dump(),
            id=product.store_slug + ":" + product.article,
            marketplace=Marketplace.OZON,
            metric_key=product.article,
            project=STORES[product.store_slug].name,
            comments=comments_for(comments, product.store_slug, product.article),
        )
        for product in sorted(
            catalog, key=lambda item: (STORES[item.store_slug].name, item.name.casefold(), item.article)
        )
    )
    return OzonAnalyticsHistory(
        rows=rows,
        weeks=tuple(
            week_info(week - timedelta(weeks=offset)) for offset in range(OZON_ANALYTICS.history_weeks)
        ),
    )


def sales(sources: OzonAnalyticsSalesSources, start: date, end: date, now: datetime) -> OzonAnalyticsSales:
    today = now.astimezone(TIME.moscow_timezone).date()
    days = dates(start, end)
    catalog = {(item.store_slug, item.article): item for item in sources.catalog}
    rows = []
    keys = set(catalog) | set(sources.turnover.products)
    for store, article in keys:
        product = catalog.get((store, article))
        saved = {item.day: item for item in sources.turnover.products.get((store, article), ())}
        known = sources.turnover.eligible_days.get(store, frozenset()) | sources.turnover.confirmed_days.get(
            store, frozenset()
        )
        counts = tuple(
            float(saved[day].ordered + saved[day].cancelled)
            if day in saved
            else 0.0
            if day in known
            else None
            for day in days
        )
        cancellations = tuple(
            float(saved[day].cancelled) if day in saved else 0.0 if day in known else None for day in days
        )
        today_value = saved.get(today)
        fact = (
            float(today_value.gross_amount)
            if today_value is not None and today_value.gross_amount is not None
            else 0.0
            if today in known
            else None
        )
        issues = tuple(
            OzonAnalyticsIssue(field=day.isoformat(), day=day, reason=OzonAnalyticsReason.MISSING_SOURCE)
            for day, value in zip(days, counts, strict=True)
            if value is None
        )
        issues += tuple(
            OzonAnalyticsIssue(field=day.isoformat(), day=day, reason=OzonAnalyticsReason.PARTIAL)
            for day, value in zip(days, counts, strict=True)
            if value is not None and day not in known
        )
        if store in sources.turnover.stale_stores:
            issues += (OzonAnalyticsIssue(field="orders", reason=OzonAnalyticsReason.STALE),)
        rows.append(
            OzonAnalyticsSalesRow(
                id=store + ":" + article,
                store_slug=store,
                marketplace=Marketplace.OZON,
                metric_key=article,
                project=STORES[store].name,
                article=article,
                name=product.name if product else article,
                image=product.image if product else "",
                category=product.category if product else "",
                barcode=product.barcode if product else "",
                barcodes=(product.barcode,) if product and product.barcode else (),
                article_aliases=(article,),
                days=counts,
                cancellations=cancellations,
                orders=total(counts),
                cancels=total(cancellations),
                known_days=sum(value is not None for value in counts),
                complete=all(
                    value is not None and day in known for day, value in zip(days, counts, strict=True)
                )
                and store not in sources.turnover.stale_stores,
                today_turnover=fact,
                field_issues=issues,
                quality={
                    **{
                        field: metric_quality(
                            complete=all(day in known for day in days),
                            known=any(value is not None for value in counts),
                            stale=store in sources.turnover.stale_stores,
                            expected_dates=days,
                            known_dates=tuple(
                                day for day, value in zip(days, counts, strict=True) if value is not None
                            ),
                        )
                        for field in ("orders", "cancels")
                    },
                    "today_turnover": metric_quality(
                        complete=today in known,
                        known=fact is not None,
                        stale=store in sources.turnover.stale_stores,
                        expected_dates=(today,),
                        known_dates=(today,) if fact is not None else (),
                    ),
                    **{
                        day.isoformat(): metric_quality(
                            complete=day in known,
                            known=value is not None,
                            stale=store in sources.turnover.stale_stores,
                            expected_dates=(day,),
                            known_dates=(day,) if value is not None else (),
                        )
                        for day, value in zip(days, counts, strict=True)
                    },
                },
            )
        )
    rows.sort(
        key=lambda row: (
            row.today_turnover is None,
            -(row.today_turnover or 0),
            row.project,
            row.name.casefold(),
            row.article,
        )
    )
    return OzonAnalyticsSales(
        rows=tuple(rows),
        dates=days,
        date_from=start,
        date_to=end,
        available_from=sources.first_day,
        available_to=sources.last_day,
        today=today,
        turnover_day=today,
        updated_at=max(sources.turnover.updated_at.values(), default=None),
        loaded_at=now.astimezone(UTC),
    )


def article_index(values: OzonArticleIndex) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values.items:
        if not value.sku.strip() or not value.article.strip():
            raise OzonAnalyticsApiError(OzonAnalyticsReason.MAPPING_CONFLICT)
        if value.sku in result and result[value.sku] != value.article:
            raise OzonAnalyticsApiError(OzonAnalyticsReason.MAPPING_CONFLICT)
        result[value.sku] = value.article
    return result


def funnel_page(rows: tuple[OzonAnalyticsApiRow, ...], mapping: dict[str, str]) -> OzonAnalyticsFunnelPage:
    values = []
    seen: set[str] = set()
    for row in rows:
        sku = row.dimensions[0].id
        if sku in seen:
            raise OzonAnalyticsApiError(OzonAnalyticsReason.REPEATED_PAGE)
        seen.add(sku)
        if sku not in mapping:
            raise OzonAnalyticsApiError(OzonAnalyticsReason.MAPPING_CONFLICT)
        values.append(OzonAnalyticsFunnelValue(sku=sku, article=mapping[sku], carts=int(row.metrics[0])))
    return OzonAnalyticsFunnelPage(items=tuple(values), rows=len(rows))
