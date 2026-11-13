from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.core.constants import ORDER_NORMALIZATION, TIME, WB_ORDER_FEED, Marketplace, OrderScheme
from app.dto.google_stock_report import (
    StockTestAccountOrders,
    StockTestOrderPeriod,
    StockTestProductOrders,
)
from app.exports import fbo_transit
from app.infrastructure.async_http import AsyncHttp, store_context
from app.orders.normalizers import yandex as yandex_normalizer
from app.ozon import tokens as ozon_tokens
from app.ozon.async_analytics import AsyncOzonAnalyticsClient
from app.ozon.async_api import AsyncSellerApi
from app.wb import order_feed
from app.wb import tokens as wb_tokens
from app.yandex import api as yandex_api
from app.yandex import tokens as yandex_tokens
from app.yandex.accounts import resolve_business_id


def order_time(raw: object) -> datetime:
    if not isinstance(raw, str) or len(raw) <= len(datetime.min.date().isoformat()):
        raise ValueError("Missing order time")
    stamp = datetime.fromisoformat(raw)
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=TIME.moscow_timezone)


def add_order(orders: dict[str, StockTestProductOrders], article: str, line: StockTestProductOrders) -> None:
    if not article or not 0 <= line.cancelled <= line.ordered:
        raise ValueError("Invalid product or order quantity")
    previous = orders.get(article)
    orders[article] = (
        line
        if previous is None
        else replace(
            previous,
            ordered=previous.ordered + line.ordered,
            cancelled=previous.cancelled + line.cancelled,
            barcode=previous.barcode or line.barcode,
            name=previous.name or line.name,
        )
    )


def wb_account(store: str, period: StockTestOrderPeriod) -> StockTestAccountOrders:
    token = wb_tokens.get_token(store)
    rows = order_feed.get_orders(
        token, period.start, datetime.now(TIME.moscow_timezone), settings.wb_sales_max_pages
    )
    orders: dict[str, StockTestProductOrders] = {}
    for row in rows:
        if not period.start <= row.created_at < period.end:
            continue
        cancelled = row.status == WB_ORDER_FEED.cancelled_status and row.updated_at < period.end
        add_order(
            orders,
            str(row.article),
            StockTestProductOrders(ordered=1, cancelled=int(cancelled)),
        )
    return StockTestAccountOrders(
        store=store, orders=orders, from_customer=fbo_transit._wb(store).from_customer
    )


def yandex_account(store: str, period: StockTestOrderPeriod) -> StockTestAccountOrders:
    key = yandex_tokens.get_api_key(store)
    campaigns = yandex_api.get_campaigns(key)
    business_id = resolve_business_id(store, key, campaigns=campaigns)
    fby = fbo_transit.yandex_fby_campaign_ids(campaigns, business_id)
    rows = yandex_api.get_business_orders(
        key, business_id, period.start.date().isoformat(), (period.end.date() + timedelta(days=1)).isoformat()
    )
    orders: dict[str, StockTestProductOrders] = {}
    for row in rows:
        if (
            row.get("fake")
            or not period.start <= order_time(row.get("creationDate") or row.get("createdAt")) < period.end
        ):
            continue
        items = row.get("items")
        if not row.get("status") or not isinstance(items, list) or not items:
            raise ValueError("Yandex did not confirm order status or items")
        for item in items:
            if not isinstance(item, dict) or not str(item.get("count", "")).isdecimal():
                raise ValueError("Yandex did not confirm item quantity")
        for line, item in zip(yandex_normalizer.normalize(store, [row]), items, strict=True):
            quantity = int(item["count"])
            add_order(
                orders,
                str(line["article"]).strip(),
                StockTestProductOrders(
                    ordered=quantity,
                    cancelled=min(int(line["cancelled_quantity"]), quantity),
                    barcode=str(line["barcode"]).strip(),
                    name=str(line["name"]).strip(),
                ),
            )
    return StockTestAccountOrders(
        store=store, orders=orders, from_customer=fbo_transit.yandex_customer_returns(key, fby)
    )


async def ozon_account(http: AsyncHttp, store: str, period: StockTestOrderPeriod) -> StockTestAccountOrders:
    context = store_context.set(store)
    try:
        credentials = ozon_tokens.get_order_credentials(store)
        client = AsyncSellerApi(http, credentials)
        start = period.start.astimezone(UTC).isoformat()
        end = (period.end - timedelta(microseconds=1)).astimezone(UTC).isoformat()
        orders: dict[str, StockTestProductOrders] = {}
        for scheme in tuple(OrderScheme) if period.start < period.end else ():
            for posting in await client.postings(scheme, start, end):
                accepted = posting.in_process_at
                if accepted is None:
                    raise ValueError("Ozon did not confirm posting processing time")
                if not period.start <= accepted < period.end:
                    continue
                cancelled = ORDER_NORMALIZATION.cancellation_status_marker in posting.status or bool(
                    posting.cancellation and posting.cancellation.cancel_reason_id
                )
                products = posting.products or posting.items
                if not products:
                    raise ValueError("Ozon did not confirm posting items")
                for product in products:
                    add_order(
                        orders,
                        product.offer_id,
                        StockTestProductOrders(
                            ordered=product.quantity,
                            cancelled=product.quantity if cancelled else 0,
                            barcode=product.barcode or "",
                            name=product.name or "",
                        ),
                    )
        snapshot = await AsyncOzonAnalyticsClient(http, credentials).transit(store, datetime.now(UTC))
        from_customer: dict[str, int] = {}
        for transit in snapshot.products:
            if transit.from_customer is None or transit.from_customer < 0 or transit.article in from_customer:
                raise ValueError("Ozon did not confirm customer returns")
            from_customer[transit.article] = transit.from_customer
        return StockTestAccountOrders(store=store, orders=orders, from_customer=from_customer)
    finally:
        store_context.reset(context)


LEGACY_ORDER_SOURCES = {Marketplace.WB: wb_account, Marketplace.YANDEX_MARKET: yandex_account}
