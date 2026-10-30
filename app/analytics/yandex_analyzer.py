from datetime import UTC, date, datetime, timedelta

from app.analytics.yandex_sources import (
    ProductSources,
    Sources,
    StoreSources,
    ad_value,
    cart_source,
    coverage,
    days,
    drr_parts,
    metric_quality,
    monday,
    order_value,
    ratio,
    sort_key,
    total,
    week_end,
)
from app.core.constants import YANDEX_ANALYTICS
from app.dto.yandex_analytics import AnalyzerResponse, AnalyzerRow, MetricQuality, Weights


def load(sources: Sources, week: date, now: datetime) -> AnalyzerResponse:
    today = sources.today
    week = monday(week)
    end = week_end(week, today)
    dates = days(week, week + timedelta(days=YANDEX_ANALYTICS.week_days - 1))
    period = days(week, end)
    traffic = days(end - timedelta(days=YANDEX_ANALYTICS.traffic_days - 1), end)
    previous = days(week - timedelta(days=YANDEX_ANALYTICS.week_days), week - timedelta(days=1))
    stock_start = monday(today) - timedelta(days=YANDEX_ANALYTICS.week_days)
    stock_days = days(stock_start, stock_start + timedelta(days=YANDEX_ANALYTICS.week_days - 1))
    yesterday = (end - timedelta(days=1)).isoformat()
    rows: list[AnalyzerRow] = []
    for store in sources.stores:
        coefficient = store.coefficients.coefficient(now.hour)
        for source in store.products.values():
            product, reference = source.product, source.reference
            if not product.name:
                continue

            def order(
                day: str, field: str, source: ProductSources = source, store: StoreSources = store
            ) -> float | None:
                return order_value(source, store, day, field, today)

            def ad(
                day: str, field: str, source: ProductSources = source, store: StoreSources = store
            ) -> float | None:
                return ad_value(source, store, day, field, today)

            previous_pairs = [(order(day, "orders_count"), order(day, "orders_amount")) for day in previous]
            previous_pairs = [
                (count, amount)
                for count, amount in previous_pairs
                if count is not None and amount is not None
            ]
            previous_count = total([pair[0] for pair in previous_pairs])
            previous_amount = total([pair[1] for pair in previous_pairs])
            previous_price = (
                round(previous_amount / previous_count, 2)
                if previous_count and previous_amount is not None
                else None
            )
            pairs = [(ad(day, "impressions"), ad(day, "clicks")) for day in period]
            pairs = [
                (impressions, clicks)
                for impressions, clicks in pairs
                if impressions is not None and clicks is not None
            ]
            impressions, clicks = total([p[0] for p in pairs]), total([p[1] for p in pairs])
            drr = drr_parts(source, store, period, today)
            yesterday_drr = drr_parts(source, store, (yesterday,), today)
            economy = [source.economy[day] for day in period if day in source.economy]
            economy_pairs = [
                item for item in economy if item.profit is not None and item.purchase is not None
            ]
            profit, purchase = (
                total([item.profit for item in economy_pairs]),
                total([item.purchase for item in economy_pairs]),
            )
            prices = source.prices
            goal = reference.goal_week
            day_goal = (
                reference.goal_day
                if reference.goal_day is not None
                else goal / YANDEX_ANALYTICS.week_days
                if goal is not None
                else None
            )
            stock_values = [product.fbs_stock, product.fbo_stock]
            stock = total(stock_values)
            demand = [order(day, "orders_count") for day in stock_days]
            complete_demand = all(value is not None for value in demand) and all(
                day in store.orders_loaded for day in stock_days
            )
            daily_demand = (
                sum(value for value in demand if value is not None) / YANDEX_ANALYTICS.week_days
                if complete_demand
                else None
            )
            fact = order(today.isoformat(), "orders_amount")
            plan = (
                round(day_goal * previous_price, 2)
                if day_goal is not None and previous_price is not None
                else None
            )
            forecast = round(fact * coefficient, 2) if fact is not None and coefficient is not None else None
            difference = round(forecast - plan, 2) if forecast is not None and plan is not None else None
            notes = tuple(
                note.model_copy(update={"can_edit": note.action_date == today.isoformat()})
                for note in source.notes
                if note.action_date >= dates[0]
            )
            cart_sources = tuple(cart_source(source, store, day) for day in traffic)
            reasons = {"carts": "source_timezone_unknown"}
            if prices.seller_price is None:
                reasons["price"] = "source_missing"
            if prices.pay_price is None:
                reasons["wallet"] = "source_missing"
            if prices.buyer_price is None:
                reasons["spp"] = "source_missing"
            if coefficient is None:
                reasons.update(
                    {key: "hourly_d_missing" for key in ("coeff", "forecast", "difference", "deviation")}
                )
            if prices.stale and prices.seller_price is not None:
                reasons["price"] = "stale_price"
            if prices.buyer_estimated:
                reasons["spp"] = "estimated_price"
            if prices.pay_estimated:
                reasons["wallet"] = "estimated_price"
            rows.append(
                AnalyzerRow(
                    id=f"{store.slug}:{product.article}",
                    store_slug=store.slug,
                    project=source.project,
                    article=product.article,
                    name=product.name,
                    barcode=product.barcode,
                    barcodes=product.barcodes,
                    image=product.image_url,
                    manager=reference.manager or "",
                    category=source.category,
                    code=reference.abc_code or "",
                    stock=stock,
                    stockPartial=any(value is None for value in stock_values),
                    stockDays=round(stock / daily_demand, 1) if stock is not None and daily_demand else None,
                    stockDailyOrders=daily_demand,
                    stockOrdersComplete=complete_demand,
                    stockStatus=reference.stock_status,
                    stockEnds=reference.stock_end_week,
                    roi=ratio(profit, purchase),
                    currentRoi=source.current.roi,
                    drr=drr.value,
                    previousPrice=previous_price,
                    impressions=tuple(ad(day, "impressions") for day in traffic),
                    clicks=tuple(ad(day, "clicks") for day in traffic),
                    ctr=tuple(ratio(ad(day, "clicks"), ad(day, "impressions")) for day in traffic),
                    carts=tuple(
                        source.funnel[day].cart_count if item.known else None
                        for day, item in zip(traffic, cart_sources, strict=True)
                    ),
                    cartSources=cart_sources,
                    spend=ad(yesterday, "spend"),
                    yesterdayDrr=yesterday_drr.value,
                    totalCtr=ratio(clicks, impressions),
                    rating=source.rating,
                    week=tuple(order(day, "orders_count") for day in dates),
                    goal=goal,
                    dayGoal=day_goal,
                    spp=ratio(prices.seller_price - prices.buyer_price, prices.seller_price)
                    if prices.seller_price is not None and prices.buyer_price is not None
                    else None,
                    wallet=prices.pay_price,
                    price=prices.seller_price,
                    coeff=coefficient,
                    fact=fact,
                    forecast=forecast,
                    difference=difference,
                    deviation=ratio(difference, plan),
                    plan=plan,
                    notes=tuple(tuple(note for note in notes if note.action_date == day) for day in dates),
                    noteHistory=notes,
                    updated={
                        "prices": prices.updated_at,
                        "reference": reference.synced_at,
                        "stock": product.mp_updated_at,
                        "orders": store.orders_updated_at,
                    },
                    coverage={
                        "orders": sum(order(day, "orders_count") is not None for day in period),
                        "advertising": sum(ad(day, "spend") is not None for day in period),
                        "expected": len(period),
                        "drr": len(drr.coverage.known),
                        "drrDetails": drr.coverage,
                        "yesterdayDrrDetails": yesterday_drr.coverage,
                        "roiComplete": len(economy_pairs) == len(period)
                        and all(item.complete for item in economy_pairs),
                        "currentRoiComplete": source.current.complete,
                        "roiMessages": tuple(message for item in economy for message in item.messages),
                        "cartsFieldCoverage": coverage(
                            traffic,
                            tuple(item.source_date.isoformat() for item in cart_sources if item.known),
                        ),
                    },
                    reasons=reasons,
                    quality={
                        "drr": drr.quality,
                        "yesterdayDrr": yesterday_drr.quality,
                        "spend": metric_quality(
                            (yesterday,),
                            (yesterday,) if ad(yesterday, "spend") is not None else (),
                            complete=yesterday in store.ads_loaded,
                            stale=bool(store.ads_error),
                        ),
                        "roi": metric_quality(
                            period,
                            tuple(item.day for item in economy_pairs),
                            complete=all(item.complete for item in economy_pairs),
                        ),
                        "currentRoi": MetricQuality(
                            complete=source.current.complete,
                            reasons=() if source.current.complete else ("partial",),
                        ),
                        "totalCtr": metric_quality(
                            period,
                            tuple(
                                day
                                for day in period
                                if ad(day, "impressions") is not None and ad(day, "clicks") is not None
                            ),
                            complete=all(day in store.ads_loaded for day in period),
                            stale=bool(store.ads_error),
                        ),
                        "stock": MetricQuality(
                            complete=all(value is not None for value in stock_values),
                            reasons=("partial",)
                            if stock is not None and any(value is None for value in stock_values)
                            else ("source_missing",)
                            if stock is None
                            else (),
                        ),
                        "stockDays": metric_quality(
                            stock_days,
                            tuple(
                                day
                                for day, value in zip(stock_days, demand, strict=True)
                                if value is not None
                            ),
                            complete=complete_demand and all(value is not None for value in stock_values),
                            stale=bool(store.orders_error),
                        ),
                        **{
                            f"day{index}": metric_quality(
                                (day,),
                                (day,) if order(day, "orders_count") is not None else (),
                                complete=day in store.orders_loaded,
                                stale=bool(store.orders_error),
                            )
                            for index, day in enumerate(dates)
                            if day <= today.isoformat()
                        },
                        **{
                            f"{metric}{index}": metric_quality(
                                (day,),
                                (day,)
                                if ad(day, "impressions") is not None
                                and (metric == "impressions" or ad(day, "clicks") is not None)
                                else (),
                                complete=day in store.ads_loaded,
                                stale=bool(store.ads_error),
                            )
                            for index, day in enumerate(traffic)
                            for metric in ("impressions", "ctr")
                        },
                    },
                    weights=Weights(
                        currentProfit=source.current.profit,
                        currentPurchase=source.current.purchase,
                        spend=drr.numerator,
                        boughtAmount=drr.denominator,
                        yesterdaySpend=yesterday_drr.numerator,
                        yesterdayAmount=yesterday_drr.denominator,
                        impressions=impressions,
                        clicks=clicks,
                        profit=profit,
                        purchase=purchase,
                        previousCount=previous_count,
                        previousAmount=previous_amount,
                    ),
                )
            )
    rows.sort(key=lambda row: sort_key(row.project, row.name, row.article, row.fact))
    return AnalyzerResponse(
        rows=tuple(rows),
        week=dates,
        traffic_days=traffic,
        today=today.isoformat(),
        anchor=end.isoformat(),
        period_from=dates[0],
        period_to=end.isoformat(),
        previous_from=previous[0],
        previous_to=previous[-1],
        stockPreviousFrom=stock_days[0],
        stockPreviousTo=stock_days[-1],
        turnover_day=today.isoformat(),
        coefficient_hour=now.hour,
        coefficient_valid_until=(
            now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        ).isoformat(),
        loaded_at=datetime.now(UTC).isoformat(),
        stores=tuple(store.slug for store in sources.stores),
    )
