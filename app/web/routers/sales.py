from fastapi import APIRouter

from app.web.routers import decision_center, rnp, sales_overview, supply, unit_economics

router = APIRouter()
router.include_router(sales_overview.router)
router.include_router(decision_center.router)
router.include_router(rnp.router)
router.include_router(unit_economics.router)
router.include_router(supply.router)
