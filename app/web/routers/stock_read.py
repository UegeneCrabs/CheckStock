from datetime import datetime

from fastapi import APIRouter, Query, Request

from app.core.domain import MOSCOW_TIMEZONE
from app.dto.stock_pages import (
    CostReportData,
    OperationData,
    StockOverview,
    StockStoreData,
    SupplyPageData,
    TotalStockData,
    WarehouseData,
)
from app.infrastructure.stock_page_repository import StockPageRepository
from app.web.stock_scope import stock_scope

router = APIRouter()


def repository(request: Request) -> StockPageRepository:
    return StockPageRepository(request.app.state.async_resources.database)


@router.get("/stock/data", response_model=StockOverview)
async def overview(request: Request) -> StockOverview:
    return await repository(request).overview(stock_scope(request))


@router.get("/stock/supplies/data", response_model=SupplyPageData)
async def supplies(request: Request) -> SupplyPageData:
    stock_scope(request)
    return await repository(request).supplies()


@router.get("/stock/total/data", response_model=TotalStockData)
async def totals(request: Request, store: str = "") -> TotalStockData:
    return await repository(request).totals(stock_scope(request, store))


@router.get("/stock/cost-report/data", response_model=CostReportData)
async def costs(
    request: Request, date_from: str = "", date_to: str = "", store: str = "", marketplace: str = ""
) -> CostReportData:
    from app.web.routers.stock_cost_report import _period

    start, end = _period(date_from, date_to)
    return await repository(request).costs(
        stock_scope(request, store, marketplace), start, end, datetime.now(MOSCOW_TIMEZONE).date()
    )


@router.get("/stock/{slug}/data", response_model=StockStoreData)
async def stock(request: Request, slug: str, mp: str = "") -> StockStoreData:
    return await repository(request).stock(stock_scope(request, slug, mp, select_marketplace=True))


@router.get("/stock/{slug}/warehouses/data", response_model=WarehouseData)
async def warehouses(request: Request, slug: str, mp: str = "") -> WarehouseData:
    return await repository(request).warehouses(stock_scope(request, slug, mp, select_marketplace=True))


@router.get("/stock/{slug}/operations/data", response_model=OperationData)
async def operations(
    request: Request, slug: str, kind: str = "", page: int = Query(default=1, ge=1)
) -> OperationData:
    from app.web.routers.stock_operations import _history_kinds

    kinds = _history_kinds(kind)
    return await repository(request).operations(
        stock_scope(request, slug), kinds, kind if kinds else "", page
    )
