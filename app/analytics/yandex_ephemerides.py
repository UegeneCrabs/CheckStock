from datetime import UTC, date, datetime, timedelta

from app.analytics import yandex_analyzer
from app.analytics.yandex_sources import (
    ProductSources,
    Sources,
    StoreSources,
    days,
    metric_quality,
    monday,
    order_value,
    ratio,
    sort_key,
    total,
    week_end,
)
from app.core.constants import YANDEX_ANALYTICS
from app.dto.yandex_analytics import (
    AnalyzerRow,
    EphemeridesResponse,
    EphemeridesRow,
    EphemeridesWeek,
    MetricQuality,
    WeekInfo,
)


def week_info(day: date) -> WeekInfo:
    year, number, _ = day.isocalendar()
    return WeekInfo(
        start=day.isoformat(),
        end=(day + timedelta(days=YANDEX_ANALYTICS.week_days - 1)).isoformat(),
        year=year,
        number=number,
    )


def period(
    source: ProductSources, store: StoreSources, selected: date, today: date, comparison: AnalyzerRow
) -> EphemeridesWeek:
    dates = days(selected, week_end(selected, today))
    candidates = [source.economy[day] for day in sorted(source.economy) if day in dates]
    prices = next((item.prices for item in reversed(candidates) if item.prices.updated_at), None)
    goals = [item.goal for item in candidates if item.goal_known]
    goal = source.reference.goal_week if selected == monday(today) else goals[-1] if goals else None
    completed = selected + timedelta(days=YANDEX_ANALYTICS.week_days - 1) < today
    facts: list[float | None] = []
    for day in dates:
        count = order_value(source, store, day, "orders_count", today)
        cancels = order_value(source, store, day, "cancel_count", today)
        facts.append(
            max(count - cancels, 0)
            if count is not None and cancels is not None and completed
            else count
            if not completed
            else None
        )
    price = prices.seller_price if prices else None
    return EphemeridesWeek(
        spp=ratio(price - prices.buyer_price, price)
        if prices and price is not None and prices.buyer_price is not None
        else None,
        wallet=prices.pay_price if prices else None,
        price=price,
        priceDay=prices.updated_at if prices else None,
        goal=goal,
        fact=total(facts),
        factNet=completed,
        turnover=goal * price if goal is not None and price is not None else None,
        impressions=comparison.weights.impressions,
        ctr=comparison.totalCtr,
        roi=comparison.roi,
        drr=comparison.drr,
        weights=comparison.weights,
        coverage=comparison.coverage,
        quality={
            "drr": comparison.quality["drr"],
            "roi": comparison.quality["roi"],
            "ctr": comparison.quality["totalCtr"],
            "impressions": comparison.quality["totalCtr"],
            "fact": metric_quality(
                dates,
                tuple(day for day, value in zip(dates, facts, strict=True) if value is not None),
                complete=all(day in store.orders_loaded for day in dates),
                stale=bool(store.orders_error),
            ),
        },
    )


def load(sources: Sources, week: date, now: datetime, *, history: bool = False) -> EphemeridesResponse:
    previous = week - timedelta(days=YANDEX_ANALYTICS.week_days)
    comparisons = (
        {
            selected: {row.id: row for row in yandex_analyzer.load(sources, selected, now).rows}
            for selected in (previous, week)
        }
        if not history
        else {}
    )
    weeks = tuple(
        week_info(week - timedelta(weeks=i))
        for i in range(YANDEX_ANALYTICS.history_weeks if history else YANDEX_ANALYTICS.decision_weeks)
    )
    rows: list[EphemeridesRow] = []
    for store in sources.stores:
        frozen = store.stock.get(week.isoformat())
        stocks = {item.article: item for item in frozen.products} if frozen else {}
        for source in store.products.values():
            product, reference = source.product, source.reference
            key = f"{store.slug}:{product.article}"
            stock = stocks.get(product.article)
            values = (
                [stock.fbs, stock.fbo, stock.ff, stock.fromCustomer]
                if stock
                else [None] * YANDEX_ANALYTICS.traffic_days
            )
            comments = {
                f"{item.week.isoformat()}:{item.kind}": item
                for item in store.comments
                if item.article == product.article
                and weeks[-1].start <= item.week.isoformat() <= week.isoformat()
            }
            current = comparisons.get(week, {}).get(key)
            rows.append(
                EphemeridesRow(
                    id=key,
                    store_slug=store.slug,
                    article=product.article,
                    name=product.name or product.article,
                    barcode=product.barcode,
                    image=product.image_url,
                    project=source.project,
                    manager=reference.manager or "",
                    category=source.category,
                    code=reference.abc_code or "",
                    fbs=stock.fbs if stock else None,
                    fbo=stock.fbo if stock else None,
                    ff=stock.ff if stock else None,
                    fromCustomer=stock.fromCustomer if stock else None,
                    stock=total(values),
                    stockPartial=any(value is None for value in values),
                    stockUpdated=frozen.captured_at.isoformat() if frozen else None,
                    transitUpdated=frozen.captured_at.isoformat() if frozen else None,
                    stockWeek=week.isoformat(),
                    previous=period(source, store, previous, sources.today, comparisons[previous][key])
                    if not history
                    else None,
                    current=period(source, store, week, sources.today, comparisons[week][key])
                    if not history
                    else None,
                    rating=source.rating,
                    dayTurnover={
                        field: getattr(current, field)
                        for field in ("plan", "fact", "forecast", "difference", "deviation")
                    }
                    if current
                    else {},
                    comments=comments,
                    quality={
                        "stock": MetricQuality(
                            complete=all(value is not None for value in values),
                            reasons=("partial",)
                            if total(values) is not None and any(value is None for value in values)
                            else ("source_missing",)
                            if total(values) is None
                            else (),
                        ),
                    },
                )
            )
    rows.sort(key=lambda row: sort_key(row.project, row.name, row.article, row.dayTurnover.get("fact")))
    return EphemeridesResponse(
        rows=tuple(rows),
        weeks=weeks,
        current=week_info(week),
        previous=week_info(previous),
        today=sources.today.isoformat(),
        through=week_end(week, sources.today).isoformat(),
        loaded_at=datetime.now(UTC).isoformat(),
    )
