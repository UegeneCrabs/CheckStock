from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app import auth, db, supply_planning
from app.domain import MOSCOW_TIMEZONE
from app.dto.supply_planning import ManualSupplyInput, ManualSupplyReadyInput
from app.web.access import accessible_store_slugs

router = APIRouter()


def _now_iso() -> str:
    return datetime.now(MOSCOW_TIMEZONE).isoformat(timespec="seconds")


def _delivery_iso(value: datetime) -> str:
    return supply_planning.normalize_datetime(value).isoformat(timespec="minutes")


def _with_urgency(rows: list[dict]) -> list[dict]:
    return [
        {**row, "is_urgent": supply_planning.is_urgent(row.get("delivery_at"))}
        for row in rows
    ]


def _guard_edit(request: Request) -> JSONResponse | None:
    if auth.can_edit_stock(request.state.user):
        return None
    return JSONResponse(
        {"ok": False, "error": "Нет права изменять план поставок"},
        status_code=403,
    )


def _actor(request: Request) -> tuple[int | None, str]:
    user = request.state.user
    return user.get("id"), str(user.get("full_name") or "Сотрудник")


@router.get("/stock/planning/wb")
async def wb_planned_supplies(
    request: Request,
    store: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
):
    allowed = accessible_store_slugs(request.state.user)
    if store:
        store_slug = store.strip().lower()
        if store_slug not in allowed:
            raise HTTPException(status_code=403, detail="Нет доступа к этому кабинету")
        allowed = (store_slug,)
    try:
        report = await run_in_threadpool(
            supply_planning.load_wb_planned_supplies,
            allowed,
            date_from=date_from,
            date_to=date_to,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return JSONResponse({"ok": True, **report})


@router.get("/stock/planning/manual")
async def manual_supplies():
    rows = await run_in_threadpool(db.list_manual_supplies)
    return JSONResponse({"ok": True, "supplies": _with_urgency(rows)})


@router.post("/stock/planning/manual")
async def create_manual_supply(request: Request, payload: ManualSupplyInput):
    denied = _guard_edit(request)
    if denied is not None:
        return denied
    user_id, user_name = _actor(request)
    now = _now_iso()
    row = await run_in_threadpool(
        db.create_manual_supply,
        _delivery_iso(payload.delivery_at),
        payload.origin,
        payload.destination,
        payload.supply_type,
        payload.ready,
        user_id,
        user_name,
        now,
    )
    await run_in_threadpool(
        db.log_action,
        user_id,
        user_name,
        "Добавлена запланированная поставка",
        f"{payload.origin} → {payload.destination} · {payload.supply_type}",
        now,
    )
    return JSONResponse({"ok": True, "supply": _with_urgency([row])[0]}, status_code=201)


@router.put("/stock/planning/manual/{supply_id}")
async def update_manual_supply(
    request: Request,
    supply_id: int,
    payload: ManualSupplyInput,
):
    denied = _guard_edit(request)
    if denied is not None:
        return denied
    now = _now_iso()
    row = await run_in_threadpool(
        db.update_manual_supply,
        supply_id,
        _delivery_iso(payload.delivery_at),
        payload.origin,
        payload.destination,
        payload.supply_type,
        payload.ready,
        now,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Поставка не найдена")
    user_id, user_name = _actor(request)
    await run_in_threadpool(
        db.log_action,
        user_id,
        user_name,
        "Изменена запланированная поставка",
        f"Поставка #{supply_id}: {payload.origin} → {payload.destination}",
        now,
    )
    return JSONResponse({"ok": True, "supply": _with_urgency([row])[0]})


@router.patch("/stock/planning/manual/{supply_id}/ready")
async def set_manual_supply_ready(
    request: Request,
    supply_id: int,
    payload: ManualSupplyReadyInput,
):
    denied = _guard_edit(request)
    if denied is not None:
        return denied
    now = _now_iso()
    row = await run_in_threadpool(db.set_manual_supply_ready, supply_id, payload.ready, now)
    if row is None:
        raise HTTPException(status_code=404, detail="Поставка не найдена")
    user_id, user_name = _actor(request)
    await run_in_threadpool(
        db.log_action,
        user_id,
        user_name,
        "Изменена готовность поставки",
        f"Поставка #{supply_id}: {'готово' if payload.ready else 'не готово'}",
        now,
    )
    return JSONResponse({"ok": True, "supply": _with_urgency([row])[0]})


@router.delete("/stock/planning/manual/{supply_id}")
async def delete_manual_supply(request: Request, supply_id: int):
    denied = _guard_edit(request)
    if denied is not None:
        return denied
    existing = await run_in_threadpool(db.get_manual_supply, supply_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Поставка не найдена")
    deleted = await run_in_threadpool(db.delete_manual_supply, supply_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Поставка не найдена")
    user_id, user_name = _actor(request)
    now = _now_iso()
    await run_in_threadpool(
        db.log_action,
        user_id,
        user_name,
        "Удалена запланированная поставка",
        f"Поставка #{supply_id}: {existing['origin']} → {existing['destination']}",
        now,
    )
    return JSONResponse({"ok": True})
