from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.access.economics_preview import require_preview
from app.access.resources import accessible_stores
from app.core.constants import TIME, Marketplace, WebErrorCode
from app.dto.ozon_commissions import OzonCommissionCalculation, OzonCommissionRequest
from app.dto.ozon_economics import OzonProductDetail, OzonProducts, OzonReportRequest
from app.dto.ozon_price_calculator import OzonPriceCalculation, OzonPriceCalculationRequest
from app.economics import ozon_commissions, ozon_price_calculator
from app.infrastructure.ozon_economics_repository import OzonEconomicsRepository
from app.web.client_page import client_page
from app.web.dependencies import AsyncResourcesDependency

router = APIRouter()


@router.get("/api/unit-economics-1c/ozon")
async def products(
    request: Request, query: Annotated[OzonReportRequest, Query()], resources: AsyncResourcesDependency
) -> OzonProducts | OzonProductDetail:
    require_preview(request.state.user)
    stores = accessible_stores(request.state.user, Marketplace.OZON)
    if (query.store and query.store not in stores) or (query.article and not query.store):
        raise HTTPException(403, WebErrorCode.FORBIDDEN)
    result = await OzonEconomicsRepository(resources.database).products(
        query.query(stores),
        datetime.now(TIME.moscow_timezone).date() - timedelta(days=1),
        bool(query.date_from),
    )
    if query.article:
        if not result.products:
            raise HTTPException(404, WebErrorCode.NOT_FOUND)
        return OzonProductDetail(product=result.products[0])
    return result


@router.get("/sales/unit-economics-1c/ozon", response_model=None)
async def page(
    request: Request, resources: AsyncResourcesDependency
) -> HTMLResponse | OzonProducts | OzonProductDetail:
    if request.query_params.get("data") == "1":
        try:
            query = OzonReportRequest.model_validate(
                {key: value for key, value in request.query_params.items() if key != "data"}
            )
        except ValidationError as error:
            raise HTTPException(422, WebErrorCode.INVALID_PERIOD) from error
        return await products(request, query, resources)
    return HTMLResponse(client_page("economics/shared/dashboard.html"))


@router.post("/api/unit-economics-1c/ozon/prices/calculate")
async def calculate_prices(request: Request, payload: OzonPriceCalculationRequest) -> OzonPriceCalculation:
    require_preview(request.state.user)
    try:
        return ozon_price_calculator.calculate(payload)
    except ValueError as error:
        raise HTTPException(422, WebErrorCode.INVALID_PRICE_CALCULATION) from error


@router.post("/api/unit-economics-1c/ozon/commission/calculate")
async def calculate_commission(request: Request, payload: OzonCommissionRequest) -> OzonCommissionCalculation:
    require_preview(request.state.user)
    return ozon_commissions.calculate(payload)
