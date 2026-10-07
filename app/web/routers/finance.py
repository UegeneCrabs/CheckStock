"""Scoped finance reads and superadmin-only setup/actions."""

from datetime import date
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.access.access_control import accessible_stores, restricts_unit_economics_to_manager
from app.access.sections import has_access
from app.application.finance import period, today
from app.core.stores import STORES
from app.dto.finance import ConnectionUpdate, CostUpdate, FinanceRunRequest
from app.dto.identity import Role, SectionName, coerce_user
from app.jobs import finance as finance_job
from app.jobs import locks
from app.jobs.tracking import queue_tracked
from app.repositories import sync_jobs
from app.web.dependencies import ContainerDependency
from app.web.templating import fill_template, render_page
from app.yandex.finance_provider import FinanceProvider, FinanceSecrets

router = APIRouter()
API = "/api/finance-reports/yandex"
ADMIN = "/api/admin/integrations/yandex-finance"


def scope(request):
    user = coerce_user(request.state.user)
    if restricts_unit_economics_to_manager(user):
        raise HTTPException(
            403, "Финансовый итог целого магазина недоступен при ограничении по товарам менеджера"
        )
    if not has_access(user, SectionName.FINANCE_YANDEX):
        raise HTTPException(403, "Нет доступа к финансовому разделу")
    stores = accessible_stores(user, "YANDEX MARKET")
    selected = request.query_params.get("store", "")
    if selected and selected not in stores:
        raise HTTPException(404, "Магазин не найден")
    return (selected,) if selected else stores


def admin(request):
    user = coerce_user(request.state.user)
    if user is None or user.role is not Role.SUPERADMIN:
        raise HTTPException(403, "Управлять финансами может только суперадминистратор")
    return str(user.id)


def store_exists(store):
    if store not in STORES:
        raise HTTPException(404, "Магазин не найден")


def query_period(request):
    try:
        return period(
            *(
                date.fromisoformat(request.query_params[key]) if request.query_params.get(key) else None
                for key in ("date_from", "date_to")
            )
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


async def read_report(request, container):
    stores = scope(request)
    start, end = query_period(request)
    return await run_in_threadpool(
        container.finance.report,
        stores,
        start,
        end,
        running=await run_in_threadpool(locks.is_running, finance_job.JOB),
    )


@router.get("/finance-reports/yandex", response_class=HTMLResponse)
async def page(request: Request):
    scope(request)
    user = coerce_user(request.state.user)
    content = fill_template(
        "finance/yandex.html", admin_hidden="" if user.role is Role.SUPERADMIN else "hidden"
    )
    return HTMLResponse(
        render_page("Финансовый отчёт · Яндекс Маркет", "finance_yandex", content, user),
        headers={"Cache-Control": "private, no-store"},
    )


@router.get(API + "/filters")
async def filters(request: Request):
    stores = scope(request)
    start, end = period()
    return {
        "stores": [{"id": s, "name": STORES[s].name} for s in stores],
        "date_from": str(start),
        "date_to": str(end),
        "today": str(today()),
    }


@router.get(API)
@router.get(API + "/summary")
async def summary(request: Request, container: ContainerDependency):
    result = await read_report(request, container)
    result.pop("_events")
    return result


@router.get(API + "/status")
async def status(request: Request, container: ContainerDependency):
    result = await read_report(request, container)
    return {key: result[key] for key in ("version", "status", "stale", "stores", "date_from", "date_to")}


@router.get(API + "/details")
async def details(request: Request, container: ContainerDependency):
    report = await read_report(request, container)
    q = request.query_params
    try:
        page_number, page_size = int(q.get("page", 1)), int(q.get("page_size", 50))
        if page_number < 1 or not 1 <= page_size <= 100:
            raise ValueError("Некорректная страница")
        return container.finance.details(
            report,
            version=q.get("version", ""),
            metric=q.get("metric", ""),
            page=page_number,
            page_size=page_size,
            day=q.get("day"),
            kind=q.get("kind"),
            category=q.get("category"),
        )
    except LookupError as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get(ADMIN + "/connections")
async def connections(request: Request, container: ContainerDependency):
    admin(request)
    return {"connections": await run_in_threadpool(container.finance.repository.connections, tuple(STORES))}


@router.get(ADMIN + "/unallocated")
async def unallocated(request: Request, container: ContainerDependency):
    admin(request)
    return {"rows": await run_in_threadpool(container.finance.repository.unallocated)}


@router.post(ADMIN + "/connections")
async def add_connection(request: Request, body: ConnectionUpdate, container: ContainerDependency):
    actor = admin(request)
    store_exists(body.store_slug)
    identifier = str(uuid4())

    def save():
        with locks.hold("yandex-finance-config"):
            # Validate presence of a key without exposing it; explicit business/campaigns required.
            secrets = FinanceSecrets()
            if body.api_key.get_secret_value():
                secrets.set(identifier, body.api_key.get_secret_value())
            else:
                secrets.get({"id": identifier, "store_slug": body.store_slug})
            return container.finance.repository.add_connection(body, actor, identifier=identifier)

    try:
        return {"connection": await run_in_threadpool(save)}
    except (ValueError, KeyError) as error:
        raise HTTPException(
            422,
            "Проверьте ключ и привязки кампаний: "
            + (str(error) if isinstance(error, ValueError) else "не задан ключ"),
        ) from None
    except locks.SyncJobBusyError:
        raise HTTPException(409, "Настройки уже изменяются") from None


class KeyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr = Field(min_length=1, max_length=16384)


class DisableRequest(BaseModel):
    effective_to: date


@router.put(ADMIN + "/connections/{identifier}/key")
async def replace_key(request: Request, identifier: str, body: KeyUpdate, container: ContainerDependency):
    admin(request)
    conns = await run_in_threadpool(container.finance.repository.connections, tuple(STORES))
    if not any(c["id"] == identifier for c in conns):
        raise HTTPException(404, "Подключение не найдено")

    def save():
        with locks.hold("yandex-finance-config"):
            FinanceSecrets().set(identifier, body.api_key.get_secret_value())

    try:
        await run_in_threadpool(save)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except locks.SyncJobBusyError:
        raise HTTPException(409, "Настройки уже изменяются") from None
    return {"ok": True}


@router.post(ADMIN + "/connections/{identifier}/disable")
async def disable(request: Request, identifier: str, body: DisableRequest, container: ContainerDependency):
    admin(request)

    def save():
        with locks.hold(finance_job.JOB), locks.hold("yandex-finance-config"):
            container.finance.repository.disable(identifier, body.effective_to)

    try:
        await run_in_threadpool(save)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except locks.SyncJobBusyError:
        raise HTTPException(409, "Дождитесь завершения текущей загрузки") from None
    return {"ok": True}


class DiscoveryRequest(BaseModel):
    store_slug: str
    api_key: SecretStr = SecretStr("")


@router.post(ADMIN + "/connections/{identifier}/check", status_code=202)
async def check_connection(request: Request, identifier: str, container: ContainerDependency):
    admin(request)
    conns = await run_in_threadpool(container.finance.repository.connections, tuple(STORES))
    connection = next((c for c in conns if c["id"] == identifier), None)
    if connection is None:
        raise HTTPException(404, "Подключение не найдено")

    def check():
        provider = FinanceProvider(container.finance.repository)
        end = today().replace(day=1)
        from datetime import timedelta

        end -= timedelta(days=1)
        try:
            for source in ("realization", "services", "orders", "payments"):
                provider.load(connection, source, end.replace(day=1), end)
            return {"ok": True, "finance_permissions_verified": True}
        except Exception as error:
            return {
                "ok": False,
                "error": getattr(error, "public_message", None)
                or f"Не удалось проверить финансовый доступ: {type(error).__name__}",
            }

    try:
        return {"run_id": await run_in_threadpool(queue_tracked, finance_job.JOB, check)}
    except locks.SyncJobBusyError:
        raise HTTPException(409, "Дождитесь завершения финансового задания") from None


@router.post(ADMIN + "/discover")
async def discover(request: Request, body: DiscoveryRequest, container: ContainerDependency):
    admin(request)
    store_exists(body.store_slug)
    try:
        return await run_in_threadpool(
            FinanceProvider(container.finance.repository).discover,
            body.store_slug,
            body.api_key.get_secret_value(),
        )
    except Exception:
        raise HTTPException(502, "Не удалось проверить кабинеты. Проверьте ключ и доступ API") from None


@router.post(ADMIN + "/runs", status_code=202)
async def run(request: Request, body: FinanceRunRequest):
    actor = admin(request)
    if body.store_slug:
        store_exists(body.store_slug)
    try:
        start, end = period(body.date_from, body.date_to)
        run_id = await run_in_threadpool(
            queue_tracked,
            finance_job.JOB,
            lambda: finance_job.run(
                (body.store_slug,) if body.store_slug else tuple(STORES), start, end, actor, body.mode
            ),
        )
        return {"ok": True, "run_id": run_id, "status": "queued"}
    except locks.SyncJobBusyError:
        raise HTTPException(409, "Финансовое задание уже выполняется") from None
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get(ADMIN + "/runs")
async def runs(request: Request):
    admin(request)
    return {
        "runs": await run_in_threadpool(sync_jobs.list_runs, finance_job.JOB, 30),
        "running": await run_in_threadpool(locks.is_running, finance_job.JOB),
    }


@router.get(ADMIN + "/costs")
async def costs(request: Request, container: ContainerDependency, store: str):
    admin(request)
    store_exists(store)
    return {"costs": await run_in_threadpool(container.finance.repository.costs, store)}


@router.post(ADMIN + "/costs")
async def cost(request: Request, body: CostUpdate, container: ContainerDependency):
    actor = admin(request)
    store_exists(body.store_slug)
    return {
        "id": await run_in_threadpool(container.finance.repository.add_cost, body, actor),
        "message": "Цена сохранена. Для изменения опубликованных расчётов запустите пересчёт периода",
    }
