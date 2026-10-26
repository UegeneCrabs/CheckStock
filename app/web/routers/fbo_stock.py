from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.access.resources import has_store_marketplace
from app.container import fbo_stock_service
from app.core.stores import STORES
from app.dto.fbo_stock import FboStockCommand, FboStockError, FboStockReport, FboStockRequest, FboTarget
from app.dto.inbound_supplies import InboundMarketplace

router = APIRouter()


def target_for(request: Request, slug: str, marketplace: InboundMarketplace) -> FboTarget:
    target = FboTarget(store_slug=slug.lower(), marketplace=marketplace)
    if target.store_slug not in STORES:
        raise HTTPException(status_code=404)
    if not has_store_marketplace(request.state.user, store_slug=target.store_slug, marketplace=marketplace):
        raise HTTPException(status_code=403)
    return target


@router.get("/stock/{slug}/fbo-stock", response_model=FboStockReport)
async def fbo_stock_data(request: Request, slug: str, mp: InboundMarketplace = "WB") -> FboStockReport:
    target = target_for(request, slug, mp)
    return await fbo_stock_service(request.app.state.async_resources).repository.report(target)


@router.post("/stock/{slug}/fbo-stock")
async def fbo_stock_action(request: Request, slug: str, payload: FboStockRequest) -> JSONResponse:
    target = target_for(request, slug, payload.marketplace)
    actor = request.state.user
    try:
        result = await fbo_stock_service(request.app.state.async_resources).execute(
            FboStockCommand(
                target=target,
                change=payload,
                request_key=request.headers.get("Idempotency-Key", ""),
                user_id=actor.id,
                user_name=actor.full_name,
            )
        )
    except FboStockError as error:
        return JSONResponse({"ok": False, "code": error.code}, status_code=400)
    return JSONResponse(result.model_dump(mode="json"))
