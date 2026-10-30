from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from app.access.economics_preview import require_preview
from app.access.resources import accessible_stores
from app.container import ozon_price_change_service
from app.core.constants import OZON_PRICE_CHANGES, HTTPStatus, Marketplace, OzonPriceChangeIssue, WebErrorCode
from app.dto.ozon_price_changes import (
    OzonPriceChangeBusyError,
    OzonPriceChangeConfirm,
    OzonPriceChangeHistory,
    OzonPriceChangeRequest,
    OzonPriceChangeView,
)
from app.dto.ozon_promotions import OzonPromotionsQuery
from app.infrastructure.ozon_price_changes_repository import OzonPriceChangeRepository
from app.ozon.tokens import OzonCredentialsNotFoundError
from app.web.dependencies import AsyncResourcesDependency

router = APIRouter(prefix=OZON_PRICE_CHANGES.api)


def authorize(request: Request, store: str) -> None:
    require_preview(request.state.user)
    if store not in accessible_stores(request.state.user, Marketplace.OZON):
        raise HTTPException(HTTPStatus.FORBIDDEN, WebErrorCode.FORBIDDEN)


@router.post("/preview")
async def preview(
    request: Request,
    payload: OzonPriceChangeRequest,
    resources: AsyncResourcesDependency,
) -> OzonPriceChangeView:
    authorize(request, payload.store)
    try:
        return await ozon_price_change_service(resources, payload.store).preview(
            payload,
            request.state.user.id,
            request.state.user.full_name,
        )
    except OzonCredentialsNotFoundError:
        raise HTTPException(HTTPStatus.BAD_GATEWAY, OzonPriceChangeIssue.ACCESS_DENIED) from None


@router.get("")
async def history(
    request: Request,
    query: Annotated[OzonPromotionsQuery, Query()],
    resources: AsyncResourcesDependency,
) -> OzonPriceChangeHistory:
    authorize(request, query.store)
    operations = await OzonPriceChangeRepository(resources.database).history(
        query.store,
        query.article,
        request.state.user.id,
    )
    from app.application.ozon_price_changes import OzonPriceChangeService

    return OzonPriceChangeHistory(items=tuple(OzonPriceChangeService.view(item) for item in operations))


@router.post("/{operation_id}/confirm")
async def confirm(
    request: Request,
    operation_id: UUID,
    store: str,
    payload: OzonPriceChangeConfirm,
    resources: AsyncResourcesDependency,
) -> OzonPriceChangeView:
    authorize(request, store)
    try:
        service = ozon_price_change_service(resources, store)
        operation = await service.owned(operation_id, request.state.user.id, store)
        result = await service.begin(operation)
        if result.start:
            resources.spawn(
                service.execute(operation_id), name=OZON_PRICE_CHANGES.api + "/" + str(operation_id)
            )
        return result.view
    except LookupError:
        raise HTTPException(HTTPStatus.NOT_FOUND, WebErrorCode.NOT_FOUND) from None
    except OzonPriceChangeBusyError:
        raise HTTPException(HTTPStatus.CONFLICT, WebErrorCode.SETTINGS_CONFLICT) from None
    except OzonCredentialsNotFoundError:
        raise HTTPException(HTTPStatus.BAD_GATEWAY, OzonPriceChangeIssue.ACCESS_DENIED) from None


@router.post("/{operation_id}/check")
async def check(
    request: Request,
    operation_id: UUID,
    store: str,
    resources: AsyncResourcesDependency,
) -> OzonPriceChangeView:
    authorize(request, store)
    try:
        service = ozon_price_change_service(resources, store)
        return await service.check(await service.owned(operation_id, request.state.user.id, store))
    except LookupError:
        raise HTTPException(HTTPStatus.NOT_FOUND, WebErrorCode.NOT_FOUND) from None
    except OzonCredentialsNotFoundError:
        raise HTTPException(HTTPStatus.BAD_GATEWAY, OzonPriceChangeIssue.ACCESS_DENIED) from None
