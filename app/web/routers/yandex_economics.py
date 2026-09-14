from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.access import auth
from app.access.access_control import has_scope
from app.access.sections import has_access
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.dto.identity import SectionAccessLevel, SectionName
from app.dto.yandex_economics import CalculationRequest, Scheme, SettingsChange
from app.repositories import yandex_assortment
from app.repositories import yandex_economics as repository
from app.yandex import economics, economics_api
from app.yandex.economics_calculation import calculate

router = APIRouter(prefix="/api/unit-economics-1c/yandex-market")

CABINET_FIELDS = {
    "fulfillment_cost",
    "storage_days",
    "tax_base",
    "tax_percent",
    "other_percent",
    "other_cost",
    "capital_percent",
    "turnover_days",
    "loss_percent",
    "disposal_cost",
    "transit_cost",
    "frequency",
    "payment_delay_weeks",
}
SCENARIO_FIELDS = {"seller_price", "buyer_price", "advertising_mode", "plan_drr"}


def authorize_settings(request, store, article, *, write=False):
    if not auth.has_role(request.state.user, "superadmin"):
        raise HTTPException(403, "Параметры доступны в разделе API-ключи и фоновые выгрузки администратору")
    if store not in STORES:
        raise HTTPException(404, "Кабинет не найден")
    authorize(request, store, article, write=write)


def settings_payload(store, article, scheme):
    saved = repository.settings(store, article, scheme)
    state = (
        economics.effective(store, article, scheme)
        if article
        else {
            "values": saved["values"],
            "origins": {key: "Настройки кабинета" for key in saved["values"]},
        }
    )
    return {
        "ok": True,
        "article": article,
        "scheme": scheme,
        "revision": saved["revision"],
        "overrides": saved["values"],
        "values": state["values"],
        "origins": state["origins"],
        "articles": sorted(yandex_assortment.active_articles(store)),
    }


@router.get("/settings/{store}")
async def read_settings(request: Request, store: str, article: str = "", scheme: Scheme = "FBY"):
    authorize_settings(request, store, article)
    return await run_in_threadpool(settings_payload, store, article, scheme)


@router.put("/settings/{store}")
async def update_settings(request: Request, store: str, payload: SettingsChange, article: str = ""):
    authorize_settings(request, store, article, write=True)
    changes = payload.values.model_dump(exclude_unset=True)
    if changes.keys() & SCENARIO_FIELDS or (not article and changes.keys() - CABINET_FIELDS):
        raise HTTPException(
            422, "Цены и план рекламы задаются в калькуляторе; закупка и тарифы — отдельно для товара"
        )
    try:
        await run_in_threadpool(
            repository.save_settings,
            store,
            article,
            payload.scheme,
            changes,
            payload.revision,
            str(request.state.user["full_name"]),
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    await run_in_threadpool(economics.capture_today, (store,), only_article=article or None)
    return await run_in_threadpool(settings_payload, store, article, payload.scheme)


def authorize(request, store, article, *, write=False):
    access = SectionAccessLevel.WRITE if write else SectionAccessLevel.READ
    if not has_scope(request.state.user, store, "YANDEX MARKET") or not has_access(
        request.state.user, SectionName.UNIT_ECONOMICS_YANDEX, access
    ):
        raise HTTPException(403, "Нет доступа к этому кабинету")
    if article and article not in yandex_assortment.active_articles(store):
        raise HTTPException(404, "Товар не входит в актуальный ассортимент YM")


@router.get("/economics/{store}/{article:path}")
async def product_economics(request: Request, store: str, article: str, scheme: Scheme = "FBY"):
    authorize(request, store, article)
    data = await run_in_threadpool(economics.detail, store, article, scheme)
    return {"ok": True, "economics": data}


@router.put("/economics/{store}/{article:path}")
async def save_economics(request: Request, store: str, article: str, payload: SettingsChange):
    authorize(request, store, article, write=True)
    try:
        await run_in_threadpool(
            repository.save_settings,
            store,
            article,
            payload.scheme,
            payload.values.model_dump(exclude_unset=True),
            payload.revision,
            str(request.state.user["full_name"]),
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    await run_in_threadpool(economics.capture_today, (store,), only_article=article)
    return {
        "ok": True,
        "economics": await run_in_threadpool(economics.detail, store, article, payload.scheme),
    }


@router.post("/calculate/{store}/{article:path}")
async def simulate(request: Request, store: str, article: str, payload: CalculationRequest):
    """Saved manual values and explicit scenario edits retain priority."""
    authorize(request, store, article)
    scenario = payload.values.model_dump(exclude_unset=True)
    state = await run_in_threadpool(
        economics.detail, store, article, payload.scheme, scenario=scenario, mode=payload.mode
    )
    if payload.refresh_tariffs:
        if payload.mode == "current":
            raise HTTPException(400, "Обновите текущий тариф отдельной кнопкой в параметрах товара.")
        try:
            quoted = await run_in_threadpool(
                economics_api.quote, store, article, payload.scheme, scenario=scenario, persist=False
            )
        except Exception as error:
            raise HTTPException(400, safe_error(error)) from error

        overrides = {
            key: value
            for key, value in state["overrides"].items()
            if key not in scenario or scenario[key] is not None
        }
        values = {
            **state["values"],
            **quoted["components"],
            **overrides,
            **{key: value for key, value in scenario.items() if value is not None},
        }
        state["values"] = values
        state["result"] = calculate(
            values, advertising_spend=state["advertising_spend"], orders_count=state["orders_count"]
        )
        state["tariff"] = {"valid": True, "services": quoted["services"], "approximate": True}
    return {"ok": True, "economics": state}


def safe_error(error):
    if isinstance(error, (ValueError, KeyError)):
        return str(error)[:400]
    return "Не удалось получить данные ЯМ. Проверьте доступ API и повторите обновление."


@router.post("/refresh-tariff/{store}/{article:path}")
async def refresh_tariff(request: Request, store: str, article: str, payload: CalculationRequest):
    authorize(request, store, article, write=True)
    try:
        await run_in_threadpool(economics_api.refresh_catalog, store)
        await run_in_threadpool(economics_api.refresh_seller_prices, store)
        await run_in_threadpool(economics_api.quote, store, article, payload.scheme)
    except Exception as error:
        raise HTTPException(400, safe_error(error)) from error
    await run_in_threadpool(economics.capture_today, (store,), only_article=article)
    return {
        "ok": True,
        "economics": await run_in_threadpool(economics.detail, store, article, payload.scheme),
    }


@router.get("/economics-history/{store}/{article:path}")
async def history(request: Request, store: str, article: str, scheme: Scheme = "FBY"):
    authorize(request, store, article)
    today = datetime.now(MOSCOW_TIMEZONE).date()
    rows = await run_in_threadpool(
        repository.history,
        store,
        (today - timedelta(days=30)).isoformat(),
        (today - timedelta(days=1)).isoformat(),
    )
    return {
        "ok": True,
        "history": [row["data"] for row in rows if row["article"] == article and row["scheme"] == scheme],
        "audit": await run_in_threadpool(repository.audit, store, article),
    }
