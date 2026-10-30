from datetime import datetime, timedelta
from decimal import DecimalException
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.access.economics_preview import require_preview
from app.access.resources import accessible_stores
from app.application.ozon_promotion_entry import OzonPromotionEntryService
from app.container import (
    ozon_cabinet_service,
    ozon_history_service,
    ozon_promotion_entry_service,
    ozon_promotions_service,
    ozon_related_service,
    ozon_tariff_service,
)
from app.core.constants import OZON_PROMOTIONS, TIME, HTTPStatus, Marketplace, WebErrorCode
from app.dto.ozon_cabinet import OzonCabinetRecord, OzonCabinets, OzonCabinetUpdate
from app.dto.ozon_calculator import OzonCalculatorRequest, OzonCalculatorValues
from app.dto.ozon_commissions import OzonCommissionCalculation, OzonCommissionRequest
from app.dto.ozon_economics import OzonProductDetail, OzonProducts, OzonReportRequest
from app.dto.ozon_history import OzonHistory, OzonHistoryQuery
from app.dto.ozon_logistics import LogisticsCalculation, LogisticsScenario
from app.dto.ozon_logistics_volume import LogisticsTariffOptions
from app.dto.ozon_price_calculator import OzonPriceCalculation, OzonPriceCalculationRequest
from app.dto.ozon_promotion_entry import (
    OzonEntryBusyError,
    OzonEntryConfirm,
    OzonEntryHistory,
    OzonEntryRequest,
    OzonEntryView,
)
from app.dto.ozon_promotions import (
    OzonBoostRecord,
    OzonBoostUpdate,
    OzonPromotionCalculation,
    OzonPromotionCalculationRequest,
    OzonPromotions,
    OzonPromotionsQuery,
)
from app.dto.ozon_related import OzonRelatedProducts, OzonRelatedQuery
from app.dto.ozon_tariffs import OzonCategorySelection, OzonTariffCatalog, OzonTariffQuery
from app.economics import ozon_commissions
from app.economics.ozon_calculator import calculate as calculate_scenario
from app.economics.ozon_logistics_volume import calculate as calculate_logistics
from app.economics.ozon_logistics_volume import tariff_options
from app.economics.ozon_promotion_calculator import calculate as calculate_promotion
from app.infrastructure.ozon_cabinet_repository import OzonCabinetConflict, OzonCabinetRepository
from app.infrastructure.ozon_economics_repository import OzonEconomicsRepository
from app.infrastructure.ozon_logistics_tariffs import CATALOG as LOGISTICS_TARIFFS
from app.infrastructure.ozon_promotion_entry_repository import OzonPromotionEntryRepository
from app.ozon.api import OzonApiError
from app.ozon.tokens import OzonCredentialsNotFoundError
from app.web.client_page import client_page
from app.web.dependencies import AsyncResourcesDependency

router = APIRouter()


def entry_access(request: Request, store: str) -> None:
    require_preview(request.state.user)
    if store not in accessible_stores(request.state.user, Marketplace.OZON):
        raise HTTPException(HTTPStatus.FORBIDDEN, WebErrorCode.FORBIDDEN)


@router.post("/api/unit-economics-1c/ozon/promotions/entry/preview")
async def entry_preview(
    request: Request, payload: OzonEntryRequest, resources: AsyncResourcesDependency
) -> OzonEntryView:
    entry_access(request, payload.store)
    try:
        return await ozon_promotion_entry_service(resources, payload.store).preview(
            payload, request.state.user.id, request.state.user.full_name
        )
    except OzonCredentialsNotFoundError:
        raise HTTPException(
            HTTPStatus.BAD_GATEWAY, WebErrorCode.OZON_PROMOTIONS_CREDENTIALS_MISSING
        ) from None


@router.get("/api/unit-economics-1c/ozon/promotions/entry")
async def entry_history(
    request: Request, query: Annotated[OzonPromotionsQuery, Query()], resources: AsyncResourcesDependency
) -> OzonEntryHistory:
    entry_access(request, query.store)
    operations = await OzonPromotionEntryRepository(resources.database).history(
        query.store, query.article, request.state.user.id
    )
    return OzonEntryHistory(items=tuple(OzonPromotionEntryService.view(item) for item in operations))


@router.post("/api/unit-economics-1c/ozon/promotions/entry/{operation_id}/confirm")
async def entry_confirm(
    request: Request,
    operation_id: UUID,
    store: str,
    payload: OzonEntryConfirm,
    resources: AsyncResourcesDependency,
) -> OzonEntryView:
    entry_access(request, store)
    try:
        service = ozon_promotion_entry_service(resources, store)
        operation = await service.owned(operation_id, request.state.user.id, store)
        result = await service.begin(operation)
        if result.start:
            resources.spawn(
                service.execute(operation_id), name=OZON_PROMOTIONS.entry_lock_prefix + str(operation_id)
            )
        return result.view
    except LookupError:
        raise HTTPException(HTTPStatus.NOT_FOUND, WebErrorCode.NOT_FOUND) from None
    except OzonEntryBusyError:
        raise HTTPException(HTTPStatus.CONFLICT, WebErrorCode.SETTINGS_CONFLICT) from None
    except OzonCredentialsNotFoundError:
        raise HTTPException(
            HTTPStatus.BAD_GATEWAY, WebErrorCode.OZON_PROMOTIONS_CREDENTIALS_MISSING
        ) from None


@router.post("/api/unit-economics-1c/ozon/promotions/entry/{operation_id}/check")
async def entry_check(
    request: Request, operation_id: UUID, store: str, resources: AsyncResourcesDependency
) -> OzonEntryView:
    entry_access(request, store)
    try:
        service = ozon_promotion_entry_service(resources, store)
        return await service.check(await service.owned(operation_id, request.state.user.id, store))
    except LookupError:
        raise HTTPException(HTTPStatus.NOT_FOUND, WebErrorCode.NOT_FOUND) from None
    except OzonCredentialsNotFoundError:
        raise HTTPException(
            HTTPStatus.BAD_GATEWAY, WebErrorCode.OZON_PROMOTIONS_CREDENTIALS_MISSING
        ) from None


@router.post("/api/unit-economics-1c/ozon/calculator/calculate")
async def calculator_scenario(request: Request, payload: OzonCalculatorRequest) -> OzonCalculatorValues:
    require_preview(request.state.user)
    try:
        return calculate_scenario(payload)
    except (ValueError, DecimalException) as error:
        raise HTTPException(422, WebErrorCode.INVALID_CALCULATOR_SCENARIO) from error


@router.get("/api/unit-economics-1c/ozon/tariffs")
async def tariffs(request: Request, resources: AsyncResourcesDependency) -> OzonTariffCatalog:
    require_preview(request.state.user)
    return ozon_tariff_service(resources).catalog


@router.get("/api/unit-economics-1c/ozon/category")
async def category(
    request: Request, query: Annotated[OzonTariffQuery, Query()], resources: AsyncResourcesDependency
) -> OzonCategorySelection:
    require_preview(request.state.user)
    if query.store not in accessible_stores(request.state.user, Marketplace.OZON):
        raise HTTPException(403, WebErrorCode.FORBIDDEN)
    try:
        return await ozon_tariff_service(resources, query.store).category(query.article)
    except (OzonApiError, OzonCredentialsNotFoundError, ValueError):
        raise HTTPException(502, WebErrorCode.OZON_CATEGORY_UNAVAILABLE) from None


@router.get("/api/unit-economics-1c/ozon/cabinets")
async def cabinets(request: Request, resources: AsyncResourcesDependency) -> OzonCabinets:
    require_preview(request.state.user)
    stores = accessible_stores(request.state.user, Marketplace.OZON)
    return OzonCabinets(items=await OzonCabinetRepository(resources.database).views(stores))


@router.get("/api/unit-economics-1c/ozon/related")
async def related_products(
    request: Request, query: Annotated[OzonRelatedQuery, Query()], resources: AsyncResourcesDependency
) -> OzonRelatedProducts:
    require_preview(request.state.user)
    if query.store not in accessible_stores(request.state.user, Marketplace.OZON):
        raise HTTPException(403, WebErrorCode.FORBIDDEN)
    try:
        return await ozon_related_service(resources, query.store).related(query.article)
    except LookupError:
        raise HTTPException(404, WebErrorCode.NOT_FOUND) from None
    except (OzonApiError, OzonCredentialsNotFoundError, ValueError):
        raise HTTPException(502, WebErrorCode.OZON_RELATED_UNAVAILABLE) from None


@router.get("/api/unit-economics-1c/ozon/history")
async def product_history(
    request: Request, query: Annotated[OzonHistoryQuery, Query()], resources: AsyncResourcesDependency
) -> OzonHistory:
    require_preview(request.state.user)
    if query.store not in accessible_stores(request.state.user, Marketplace.OZON):
        raise HTTPException(403, WebErrorCode.FORBIDDEN)
    try:
        return await ozon_history_service(resources).history(query, datetime.now(TIME.moscow_timezone).date())
    except LookupError:
        raise HTTPException(404, WebErrorCode.NOT_FOUND) from None


@router.get("/api/unit-economics-1c/ozon/promotions")
async def product_promotions(
    request: Request, query: Annotated[OzonPromotionsQuery, Query()], resources: AsyncResourcesDependency
) -> OzonPromotions:
    require_preview(request.state.user)
    if query.store not in accessible_stores(request.state.user, Marketplace.OZON):
        raise HTTPException(HTTPStatus.FORBIDDEN, WebErrorCode.FORBIDDEN)
    try:
        return await ozon_promotions_service(resources, query.store).promotions(query.article)
    except LookupError:
        raise HTTPException(HTTPStatus.NOT_FOUND, WebErrorCode.NOT_FOUND) from None
    except OzonCredentialsNotFoundError:
        raise HTTPException(
            HTTPStatus.BAD_GATEWAY, WebErrorCode.OZON_PROMOTIONS_CREDENTIALS_MISSING
        ) from None
    except OzonApiError as error:
        code = (
            WebErrorCode.OZON_PROMOTIONS_ACCESS_DENIED
            if error.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)
            else WebErrorCode.OZON_PROMOTIONS_UNAVAILABLE
        )
        raise HTTPException(HTTPStatus.BAD_GATEWAY, code) from None
    except (ValueError, TimeoutError):
        raise HTTPException(HTTPStatus.BAD_GATEWAY, WebErrorCode.OZON_PROMOTIONS_UNAVAILABLE) from None


@router.post("/api/unit-economics-1c/ozon/promotions/calculate")
async def promotion_scenario(
    request: Request, payload: OzonPromotionCalculationRequest, resources: AsyncResourcesDependency
) -> OzonPromotionCalculation:
    require_preview(request.state.user)
    try:
        settings = await OzonCabinetRepository(resources.database).boost_settings()
        return calculate_promotion(payload, ozon_tariff_service(resources).calculate, settings.values)
    except (ValueError, DecimalException) as error:
        raise HTTPException(
            HTTPStatus.UNPROCESSABLE_ENTITY, WebErrorCode.INVALID_CALCULATOR_SCENARIO
        ) from error


@router.get("/api/unit-economics-1c/ozon/promotions/settings")
async def promotion_settings(request: Request, resources: AsyncResourcesDependency) -> OzonBoostRecord:
    require_preview(request.state.user)
    return await OzonCabinetRepository(resources.database).boost_settings()


@router.put("/api/unit-economics-1c/ozon/promotions/settings")
async def save_promotion_settings(
    request: Request, payload: OzonBoostUpdate, resources: AsyncResourcesDependency
) -> OzonBoostRecord:
    require_preview(request.state.user)
    try:
        return await ozon_cabinet_service(resources).save_boost(payload, request.state.user.full_name)
    except OzonCabinetConflict as error:
        raise HTTPException(HTTPStatus.CONFLICT, WebErrorCode.SETTINGS_CONFLICT) from error


@router.get("/api/unit-economics-1c/ozon/cabinets/{store}")
async def cabinet(request: Request, store: str, resources: AsyncResourcesDependency) -> OzonCabinetRecord:
    require_preview(request.state.user)
    if store not in accessible_stores(request.state.user, Marketplace.OZON):
        raise HTTPException(403, WebErrorCode.FORBIDDEN)
    records = await OzonCabinetRepository(resources.database).settings((store,))
    return records[0]


@router.put("/api/unit-economics-1c/ozon/cabinets/{store}")
async def save_cabinet(
    request: Request,
    store: str,
    payload: OzonCabinetUpdate,
    resources: AsyncResourcesDependency,
) -> OzonCabinetRecord:
    require_preview(request.state.user)
    if store not in accessible_stores(request.state.user, Marketplace.OZON):
        raise HTTPException(403, WebErrorCode.FORBIDDEN)
    try:
        return await ozon_cabinet_service(resources).save(store, payload, request.state.user.full_name)
    except OzonCabinetConflict as error:
        raise HTTPException(409, WebErrorCode.SETTINGS_CONFLICT) from error


@router.post("/api/unit-economics-1c/ozon/logistics/calculate")
async def logistics_calculation(request: Request, payload: LogisticsScenario) -> LogisticsCalculation:
    require_preview(request.state.user)
    try:
        return calculate_logistics(payload, LOGISTICS_TARIFFS, datetime.now(TIME.moscow_timezone).date())
    except (ValueError, DecimalException) as error:
        raise HTTPException(422, WebErrorCode.INVALID_CALCULATOR_SCENARIO) from error


@router.get("/api/unit-economics-1c/ozon/logistics/tariffs")
async def logistics_tariffs(request: Request) -> LogisticsTariffOptions:
    require_preview(request.state.user)
    return tariff_options(LOGISTICS_TARIFFS, datetime.now(TIME.moscow_timezone).date())


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
async def calculate_prices(
    request: Request, payload: OzonPriceCalculationRequest, resources: AsyncResourcesDependency
) -> OzonPriceCalculation:
    require_preview(request.state.user)
    try:
        return ozon_tariff_service(resources).calculate(payload)
    except ValueError as error:
        raise HTTPException(422, WebErrorCode.INVALID_PRICE_CALCULATION) from error


@router.post("/api/unit-economics-1c/ozon/commission/calculate")
async def calculate_commission(request: Request, payload: OzonCommissionRequest) -> OzonCommissionCalculation:
    require_preview(request.state.user)
    return ozon_commissions.calculate(payload)
