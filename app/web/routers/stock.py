from fastapi import APIRouter

from app.web.routers import (
    stock_cost_report,
    stock_mutations,
    stock_operations,
    stock_pages,
    stock_supplies,
    stock_total,
)

router = APIRouter()
router.include_router(stock_cost_report.router)
router.include_router(stock_total.router)
router.include_router(stock_pages.router)
router.include_router(stock_mutations.router)
router.include_router(stock_operations.router)
router.include_router(stock_supplies.router)
