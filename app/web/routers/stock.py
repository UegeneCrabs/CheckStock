from fastapi import APIRouter

from app.web.routers import stock_mutations, stock_operations, stock_pages, stock_supplies

router = APIRouter()
router.include_router(stock_pages.router)
router.include_router(stock_mutations.router)
router.include_router(stock_operations.router)
router.include_router(stock_supplies.router)
