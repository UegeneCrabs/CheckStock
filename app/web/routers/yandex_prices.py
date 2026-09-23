import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Lock

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app import db
from app.dto.yandex_prices import PriceConfirmation, PricePreview
from app.web.routers.yandex_economics import authorize
from app.yandex import api, prices

router = APIRouter(prefix="/api/unit-economics-1c/yandex-market/prices")
logger = logging.getLogger(__name__)
EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ym-price")
LOCK = Lock()
PREVIEW_TTL = 600
JOB_TTL = 86400


def state(request):
    # Like WB, jobs belong to this web process. Never persist API credentials in a job.
    if not hasattr(request.app.state, "yandex_price_jobs"):
        request.app.state.yandex_price_jobs = {}
    entries = request.app.state.yandex_price_jobs
    now = time.time()
    for entry_id, entry in list(entries.items()):
        ttl = PREVIEW_TTL if entry["status"] == "preview" else JOB_TTL
        if entry["status"] not in {"queued", "running"} and now - entry["created"] > ttl:
            del entries[entry_id]
    return entries


def owned_entry(request, entry_id):
    entry = state(request).get(entry_id)
    if not entry or entry["user_id"] != request.state.user["id"]:
        raise HTTPException(404, "Отправка не найдена или подтверждение устарело. Рассчитайте цену заново")
    authorize(request, entry["plan"]["store_slug"], entry["plan"]["article"], write=True)
    return entry


@router.post("/preview")
async def preview(request: Request, payload: PricePreview):
    authorize(request, payload.store_slug, payload.article, write=True)
    try:
        plan = await run_in_threadpool(
            prices.preview, payload.store_slug, payload.article, payload.seller_price
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except KeyError as error:
        raise HTTPException(422, str(error.args[0])) from error
    except api.YandexApiError as error:
        raise HTTPException(502, str(error)) from error
    preview_id = uuid.uuid4().hex
    with LOCK:
        state(request)[preview_id] = {
            "user_id": request.state.user["id"],
            "created": time.time(),
            "plan": plan,
            "status": "preview",
        }
    return {"ok": True, "preview_id": preview_id, "plan": plan}


def run_job(entry, user):
    plan = entry["plan"]
    with LOCK:
        entry["status"] = "running"

    def sent():
        with LOCK:
            entry["sent"] = True
        try:
            db.log_action(
                int(user["id"]),
                str(user["full_name"]),
                "yandex_price_submit",
                f"Цена ЯМ: {plan['store_slug']}, SKU {plan['article']}, "
                f"кабинет {plan['business_id']}: {plan['previous_price']['value']} → {plan['price']['value']} ₽",
                datetime.now(UTC).isoformat(),
            )
        except Exception:
            logger.exception("yandex_price_audit_failed")

    try:
        result = prices.apply(plan, sent)
    except Exception as error:
        logger.exception("yandex_price_job_failed store=%s article=%s", plan["store_slug"], plan["article"])
        message = (
            str(error) if isinstance(error, (ValueError, api.YandexApiError)) else "Ошибка отправки цены ЯМ"
        )
        if entry.get("sent"):
            message = "Запрос принят Маркетом. " + message
        result = {"status": "error", "error": message}
    with LOCK:
        result.update(store_slug=plan["store_slug"], article=plan["article"])
        entry.update(result=result, status=result["status"], error=result.get("error"))


@router.post("")
async def submit(request: Request, payload: PriceConfirmation):
    with LOCK:
        entry = owned_entry(request, payload.preview_id)
        # A retry of the same confirmation returns the same job, even after it finished.
        if entry["status"] != "preview":
            return JSONResponse({"ok": True, "job_id": payload.preview_id}, status_code=202)
        plan = entry["plan"]
        if any(
            other["status"] in {"queued", "running"}
            and other["plan"]["business_id"] == plan["business_id"]
            and other["plan"]["article"] == plan["article"]
            for other in state(request).values()
        ):
            raise HTTPException(409, "Цена этого товара уже отправляется. Дождитесь завершения")
        entry["status"] = "queued"
        try:
            EXECUTOR.submit(run_job, entry, dict(request.state.user))
        except Exception:
            entry["status"] = "preview"
            raise
    return JSONResponse({"ok": True, "job_id": payload.preview_id}, status_code=202)


@router.get("/jobs/{job_id}")
async def job(request: Request, job_id: str):
    with LOCK:
        entry = owned_entry(request, job_id)
        if entry["status"] == "preview":
            raise HTTPException(404, "Отправка ещё не подтверждена")
        return {
            "ok": True,
            "id": job_id,
            "status": entry["status"],
            "result": entry.get("result"),
            "error": entry.get("error"),
        }
