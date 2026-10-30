from datetime import UTC, date, datetime, timedelta

from app.analytics.yandex_sources import Sources, days, metric_quality, order_value, sort_key, total
from app.core.constants import YANDEX_ANALYTICS
from app.dto.yandex_analytics import SalesResponse, SalesRow


def load(sources: Sources, start: date | None, end: date | None) -> SalesResponse:
    first = min((store.first_day for store in sources.stores if store.first_day), default=None)
    last = max((store.last_day for store in sources.stores if store.last_day), default=None)
    end = end or (min(sources.today, date.fromisoformat(last)) if last else sources.today)
    start = start or end - timedelta(days=YANDEX_ANALYTICS.week_days - 1)
    dates = days(start, end)
    rows: list[SalesRow] = []
    for store in sources.stores:
        for source in store.products.values():
            product = source.product
            orders = tuple(order_value(source, store, day, "orders_count", sources.today) for day in dates)
            cancels = tuple(order_value(source, store, day, "cancel_count", sources.today) for day in dates)
            known = sum(value is not None for value in orders)
            source_complete = all(day in store.orders_loaded for day in dates)
            rows.append(
                SalesRow(
                    id=f"{store.slug}:{product.article}",
                    store_slug=store.slug,
                    project=source.project,
                    article=product.article,
                    name=product.name or product.article,
                    image=product.image_url,
                    category=source.category,
                    barcode=product.barcode or ", ".join(product.barcodes),
                    barcodes=product.barcodes,
                    article_aliases=tuple(set((*product.article_aliases, product.article))),
                    days=orders,
                    cancellations=cancels,
                    orders=total(orders),
                    cancels=total(cancels),
                    known_days=known,
                    complete=known == len(dates) and source_complete and not store.orders_error,
                    today_turnover=order_value(
                        source, store, sources.today.isoformat(), "orders_amount", sources.today
                    ),
                    quality={
                        "today_turnover": metric_quality(
                            (sources.today.isoformat(),),
                            (sources.today.isoformat(),)
                            if order_value(
                                source, store, sources.today.isoformat(), "orders_amount", sources.today
                            )
                            is not None
                            else (),
                            complete=sources.today.isoformat() in store.orders_loaded,
                            stale=bool(store.orders_error),
                        ),
                        "orders": metric_quality(
                            dates,
                            tuple(day for day, value in zip(dates, orders, strict=True) if value is not None),
                            complete=source_complete,
                            stale=bool(store.orders_error),
                        ),
                        "cancels": metric_quality(
                            dates,
                            tuple(
                                day for day, value in zip(dates, cancels, strict=True) if value is not None
                            ),
                            complete=source_complete,
                            stale=bool(store.orders_error),
                        ),
                        **{
                            day: metric_quality(
                                (day,),
                                (day,) if value is not None else (),
                                complete=day in store.orders_loaded,
                                stale=bool(store.orders_error),
                            )
                            for day, value in zip(dates, orders, strict=True)
                        },
                    },
                )
            )
    rows.sort(key=lambda row: sort_key(row.project, row.name, row.article, row.today_turnover))
    return SalesResponse(
        rows=tuple(rows),
        dates=dates,
        date_from=dates[0],
        date_to=dates[-1],
        available_from=first,
        available_to=last,
        today=sources.today.isoformat(),
        turnover_day=sources.today.isoformat(),
        updated_at=max(
            (store.orders_updated_at for store in sources.stores if store.orders_updated_at), default=None
        ),
        loaded_at=datetime.now(UTC).isoformat(),
    )
