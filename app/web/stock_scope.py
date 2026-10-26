from fastapi import HTTPException, Request

from app.access.authentication import is_active_user
from app.access.resources import store_marketplace_pairs
from app.core.constants import WebErrorCode
from app.core.stores import STORES
from app.dto.stock_pages import StockPageQuery, StockTarget
from app.web.access import accessible_store_slugs


def stock_scope(
    request: Request, store: str = "", marketplace: str = "", *, select_marketplace: bool = False
) -> StockPageQuery:
    user = request.state.user
    stores = accessible_store_slugs(user)
    if not is_active_user(user):
        raise HTTPException(status_code=403, detail=WebErrorCode.FORBIDDEN)
    store = store.strip().lower()
    if store and store not in STORES:
        raise HTTPException(status_code=404, detail=WebErrorCode.NOT_FOUND)
    if store and store not in stores:
        raise HTTPException(status_code=403, detail=WebErrorCode.FORBIDDEN)
    targets = tuple(
        StockTarget(store_slug=slug, marketplace=mp) for slug, mp in store_marketplace_pairs(user)
    )
    allowed = tuple(
        dict.fromkeys(target.marketplace for target in targets if not store or target.store_slug == store)
    )
    if marketplace and marketplace not in allowed:
        raise HTTPException(status_code=403, detail=WebErrorCode.FORBIDDEN)
    if select_marketplace:
        if not allowed:
            raise HTTPException(status_code=403, detail=WebErrorCode.FORBIDDEN)
        marketplace = marketplace or allowed[0]
    return StockPageQuery(stores=stores, targets=targets, store=store, marketplace=marketplace, can_edit=True)
