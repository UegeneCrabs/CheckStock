import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from app.core.constants import GOOGLE_STOCK_TEST, TIME, Marketplace, WeekIssue
from app.core.export_journal import diagnostic
from app.core.stores import STORES
from app.dto.google_stock_report import (
    StockTable,
    StockTestAccountOrders,
    StockTestCatalog,
    StockTestOrderPeriod,
)
from app.dto.google_week import WeekError
from app.exports.project_scope import stores_for_marketplace
from app.exports.project_sheet import EXPORT_HEADERS


def order_period(now: datetime) -> StockTestOrderPeriod:
    current = now.astimezone(TIME.moscow_timezone)
    monday = (current - timedelta(days=current.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    hour, minute = map(int, GOOGLE_STOCK_TEST.default_time.split(":"))
    friday = (monday + timedelta(days=GOOGLE_STOCK_TEST.default_weekday)).replace(hour=hour, minute=minute)
    return StockTestOrderPeriod(start=monday, end=min(friday, current.replace(microsecond=0)))


def order_table(catalog: StockTestCatalog, accounts: tuple[StockTestAccountOrders, ...]) -> StockTable:
    orders = {
        (account.store, article): row for account in accounts for article, row in account.orders.items()
    }
    returns = {
        (account.store, article): count
        for account in accounts
        for article, count in account.from_customer.items()
        if count
    }
    products = {(item.store, item.article): item for item in catalog.products}
    rows: list[tuple[str | int, ...]] = []
    for key in sorted(products.keys() | orders.keys() | returns.keys()):
        store, article = key
        product = products.get(key)
        order = orders.get(key)
        barcode = (product.barcode if product else "") or (order.barcode if order else "")
        name = (product.name if product else "") or (order.name if order else "")
        barcode = barcode.strip().removeprefix("'").strip()
        project = STORES[store].name
        rows.append(
            (
                f"{project} {barcode}",
                project,
                article,
                barcode,
                name,
                returns.get(key, 0),
                order.ordered - order.cancelled if order else 0,
            )
        )
    return StockTable(
        headers=(*EXPORT_HEADERS[:5], GOOGLE_STOCK_TEST.returns_header, GOOGLE_STOCK_TEST.orders_header),
        rows=tuple(rows),
        warnings=(),
    )


class GoogleTestOrderReport:
    def __init__(
        self,
        read_catalog: Callable[[Marketplace], Awaitable[StockTestCatalog]],
        read_account: Callable[[Marketplace, str, StockTestOrderPeriod], Awaitable[StockTestAccountOrders]],
    ):
        self.read_catalog = read_catalog
        self.read_account = read_account

    async def load(self, marketplace: Marketplace, period: StockTestOrderPeriod) -> StockTable:
        async def account(store: str) -> StockTestAccountOrders | str:
            try:
                return await self.read_account(marketplace, store, period)
            except Exception as error:
                status = getattr(error, "status", None)
                diagnostic(
                    "google_test_order_source_failed",
                    marketplace=marketplace,
                    store=store,
                    status=status,
                    detail=f"{type(error).__name__}: {error}",
                )
                return (
                    f"{STORES[store].name} (HTTP {status})"
                    if isinstance(status, int)
                    else (f"{STORES[store].name} ({type(error).__name__})")
                )

        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(account(store)) for store in stores_for_marketplace(marketplace.value)]
        results = tuple(task.result() for task in tasks)
        errors = [result for result in results if isinstance(result, str)]
        if errors:
            raise WeekError(WeekIssue.SOURCE_INCOMPLETE, detail=", ".join(errors))
        return order_table(
            await self.read_catalog(marketplace),
            tuple(result for result in results if isinstance(result, StockTestAccountOrders)),
        )
