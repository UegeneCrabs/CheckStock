import json
import logging

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from app.dto.unit_economics import (
    FulfillmentRatesRequest,
    UnitEconomicsCalculationRequest,
)
from app.errors import UnitEconomicsValidationError
from app.stores import STORES
from app.wb import unit_economics as wb_unit_economics
from app.web.access import accessible_store_items, has_store_access
from app.web.dependencies import UnitEconomicsConfigurationServiceDependency
from app.web.routers.sales_common import render_sales_placeholder
from app.web.templating import fill_template, read_template, render_page

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/sales/unit-economics", response_class=HTMLResponse)
async def sales_unit_economics(request: Request):
    return render_page(
        "CheckStock — Юнит-экономика",
        "sales_unit",
        read_template("sales_unit_content.html"),
        request.state.user,
    )


@router.get("/sales/unit-economics/wb-fbs", response_class=HTMLResponse)
async def sales_unit_economics_wb_fbs(
    request: Request,
    configuration_service: UnitEconomicsConfigurationServiceDependency,
):
    accessible = accessible_store_items(request.state.user)
    unit_config = await run_in_threadpool(configuration_service.configuration, accessible)
    content = fill_template(
        "unit_economics_wb_content.html",
        unit_config=json.dumps(unit_config.model_dump(mode="json"), ensure_ascii=False).replace("</", "<\\/"),
    )
    return render_page(
        "CheckStock — Юнит-экономика WB FBS",
        "sales_wb_fbs",
        content,
        request.state.user,
    )


@router.post("/sales/unit-economics/wb-fbs/calculate")
async def sales_unit_economics_wb_fbs_calculate(
    request: Request,
    payload: UnitEconomicsCalculationRequest,
):
    store_slug = payload.store.lower()
    if store_slug not in STORES:
        return JSONResponse({"ok": False, "error": "Кабинет не найден"}, status_code=404)
    if not has_store_access(request.state.user, store_slug):
        return JSONResponse({"ok": False, "error": "Нет доступа к этому магазину"}, status_code=403)

    try:
        return await run_in_threadpool(wb_unit_economics.load_wb_fbs_data, store_slug)
    except Exception as error:
        logger.exception("Юнит-экономика WB %s: данные не собраны", store_slug)
        return JSONResponse(
            {"ok": False, "error": f"Не удалось обновить расчет: {error}"},
            status_code=502,
        )


@router.post("/sales/unit-economics/wb-fbs/fulfillment-rates")
async def sales_unit_economics_wb_fbs_fulfillment_rates(
    payload: FulfillmentRatesRequest,
    configuration_service: UnitEconomicsConfigurationServiceDependency,
):
    try:
        result = await run_in_threadpool(configuration_service.save_rates, payload)
    except UnitEconomicsValidationError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=422)
    except Exception:
        logger.exception("Тарифы фулфилментов не сохранены")
        return JSONResponse(
            {"ok": False, "error": "Не удалось сохранить тарифы. Повторите ещё раз"},
            status_code=500,
        )
    return {"ok": True, **result.model_dump(mode="json")}


@router.get("/sales/unit-economics/ozon", response_class=HTMLResponse)
async def sales_unit_economics_ozon(request: Request):
    return render_sales_placeholder(
        request,
        "Юнит-экономика Ozon",
        "sales_ozon",
        "Расчет для кабинетов Ozon будет подключен в этом разделе.",
    )


@router.get("/sales/unit-economics/yandex-market", response_class=HTMLResponse)
async def sales_unit_economics_yandex(request: Request):
    return render_sales_placeholder(
        request,
        "Юнит-экономика Яндекс Маркет",
        "sales_yandex",
        "Расчет для кабинетов Яндекс Маркета будет подключен в этом разделе.",
    )
