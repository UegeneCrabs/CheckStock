from http import HTTPStatus

from fastapi import APIRouter, HTTPException, Request

from app.access.authentication import require_active_user
from app.access.economics_preview import require_preview
from app.access.resources import accessible_stores
from app.core.constants import Marketplace, WebErrorCode
from app.dto.product_comments import (
    ImportedProductComments,
    ProductComment,
    ProductCommentImport,
    ProductComments,
)
from app.infrastructure.product_comments_repository import ProductCommentsRepository
from app.web.dependencies import AsyncResourcesDependency

router = APIRouter(prefix="/api/unit-economics-1c/product-comments/{marketplace}")


def comment_stores(request: Request, marketplace: Marketplace) -> tuple[str, ...]:
    user = require_active_user(request)
    if marketplace is Marketplace.OZON:
        require_preview(user)
    return accessible_stores(user, marketplace)


@router.get("")
async def load_comments(
    request: Request, marketplace: Marketplace, resources: AsyncResourcesDependency
) -> ProductComments:
    stores = comment_stores(request, marketplace)
    return ProductComments(
        comments=await ProductCommentsRepository(resources.database).load(marketplace, stores)
    )


@router.put("")
async def save_comment(
    request: Request, marketplace: Marketplace, payload: ProductComment, resources: AsyncResourcesDependency
) -> ProductComment:
    if payload.store_slug not in comment_stores(request, marketplace):
        raise HTTPException(HTTPStatus.FORBIDDEN, WebErrorCode.FORBIDDEN)
    if not await ProductCommentsRepository(resources.database).save(marketplace, payload):
        raise HTTPException(HTTPStatus.NOT_FOUND, WebErrorCode.NOT_FOUND)
    return payload


@router.post("/import")
async def import_comments(
    request: Request,
    marketplace: Marketplace,
    payload: ProductCommentImport,
    resources: AsyncResourcesDependency,
) -> ImportedProductComments:
    stores = frozenset(comment_stores(request, marketplace))
    comments = tuple(comment for comment in payload.comments if comment.store_slug in stores)
    accepted = await ProductCommentsRepository(resources.database).import_missing(marketplace, comments)
    return ImportedProductComments(accepted=accepted)
