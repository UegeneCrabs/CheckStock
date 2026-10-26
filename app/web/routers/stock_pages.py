import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.core.constants import WebErrorCode
from app.infrastructure.stock_page_repository import StockPageRepository
from app.web.client_page import client_page
from app.web.stock_scope import stock_scope

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/stock", response_class=HTMLResponse)
async def stock(request: Request):
    stock_scope(request)
    return client_page("stock/index.html")


@router.get("/stock/supplies", response_class=HTMLResponse)
async def stock_supplies(request: Request):
    stock_scope(request)
    return client_page("stock/supplies.html")


@router.get("/stock/{slug}", response_class=HTMLResponse)
async def stock_store(request: Request, slug: str, mp: str = ""):
    stock_scope(request, slug, mp, select_marketplace=True)
    return client_page("stock/store.html")


@router.get("/stock/{slug}/total-data")
async def stock_store_total_data(request: Request, slug: str):
    return await StockPageRepository(request.app.state.async_resources.database).totals(
        stock_scope(request, slug)
    )


@router.get("/stock/{slug}/fbs")
async def stock_store_fbs_by_ff(request: Request, slug: str, ff: str = "", mp: str = ""):

    return await StockPageRepository(request.app.state.async_resources.database).fbs(
        stock_scope(request, slug, mp, select_marketplace=True), ff
    )


@router.get("/stock/{slug}/ff-available")
async def stock_store_ff_available(request: Request, slug: str, ff: str = "", mp: str = ""):

    return await StockPageRepository(request.app.state.async_resources.database).fulfillment(
        stock_scope(request, slug, mp, select_marketplace=True), ff
    )


@router.get("/stock/{slug}/transit")
async def stock_store_transit(request: Request, slug: str, ff: str = "", mp: str = ""):
    return await StockPageRepository(request.app.state.async_resources.database).transit(
        stock_scope(request, slug, mp, select_marketplace=True), ff
    )


@router.get("/stock/{slug}/article-detail")
async def stock_store_article_detail(request: Request, slug: str, article: str = "", mp: str = ""):

    if not article.strip():
        raise HTTPException(status_code=400, detail=WebErrorCode.INVALID_ARTICLE)
    return await StockPageRepository(request.app.state.async_resources.database).article(
        stock_scope(request, slug, mp, select_marketplace=True), article.strip()
    )
