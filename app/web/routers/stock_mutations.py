import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app import access_notifications, auth, db
from app.access_control import ActionPermission, has_action_permission
from app.dto.marketplace import Marketplace
from app.dto.stock import (
    AddFulfillmentItemsCommand,
    AddFulfillmentItemsRequest,
    CancelTransitCommand,
    CancelTransitRequest,
    ReceiveTransitCommand,
    ReceiveTransitRequest,
    ReopenTransitCommand,
    ReopenTransitRequest,
    ShipmentCommand,
    SignedStockEntries,
    TransferStockCommand,
)
from app.errors import StockValidationError
from app.ff_import import importer as ff_stock_import
from app.ff_import import shipment as ff_shipment
from app.ff_import import transfer as ff_transfer
from app.formatting import format_dt
from app.stores import STORES
from app.web.access import accessible_marketplaces
from app.web.common import _now_iso
from app.web.dependencies import StockMovementServiceDependency

router = APIRouter()
logger = logging.getLogger(__name__)


def _required_note(value: str) -> tuple[str, JSONResponse | None]:
    note = value.strip()
    if note:
        return note, None
    return "", JSONResponse(
        {"ok": False, "error": "Укажите примечание: что, зачем и по какой причине проводится"},
        status_code=400,
    )


def _guard_stock_action(
    actor,
    *,
    permission: ActionPermission,
    store_slug: str,
    marketplace: str,
    target_marketplace: str | None = None,
    reason: str,
    context: dict | None = None,
) -> JSONResponse | None:
    if actor.access_profile is None:
        denied = _guard_stock_edit(actor)
        if denied:
            return JSONResponse({"ok": False, "error": denied}, status_code=403)
    if has_action_permission(
        actor,
        permission,
        store_slug=store_slug,
        marketplace=marketplace,
        target_marketplace=target_marketplace,
    ):
        return None
    if actor.access_profile is None or not accessible_marketplaces(actor, store_slug):
        return JSONResponse({"ok": False, "error": "Нет доступа к этой площадке"}, status_code=403)
    access_request = db.create_access_request(
        user_id=actor.id,
        permission=permission.value,
        store_slug=store_slug,
        source_marketplace=marketplace,
        target_marketplace=target_marketplace,
        reason=reason.strip() or "Рабочая операция со стоком",
        context=context or {},
        duration_days=7,
    )
    if access_request.get("created_new"):
        access_notifications.notify_request_created(access_request)
    return JSONResponse(
        {
            "ok": False,
            "approval_required": True,
            "request_id": access_request["id"],
            "error": "Нужен доступ супер-администратора. Запрос уже отправлен; после одобрения разрешение будет действовать 7 дней.",
        },
        status_code=403,
    )


@router.post("/stock/{slug}/upload-ff-stock")
async def upload_ff_stock(
    request: Request,
    slug: str,
    fulfillment: str = Form(...),
    marketplace: Marketplace = Form(Marketplace.WB),
    note: str = Form("", max_length=200),
    preview: str = Form(""),
    confirmation_token: str = Form("", max_length=128),
    sheet_url: str = Form(""),
    file: UploadFile | None = File(None),
):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    note_text, note_error = _required_note(note)
    if note_error is not None:
        return note_error

    fulfillment = fulfillment.strip()
    if not fulfillment:
        return JSONResponse({"ok": False, "error": "Выберите фулфилмент назначения"}, status_code=400)

    preview_requested = preview.strip().lower() in ("1", "true", "on", "yes")
    if not preview_requested:
        denied = await run_in_threadpool(
            _guard_stock_action,
            request.state.user,
            permission=ActionPermission.STOCK_RECEIVE,
            store_slug=slug.lower(),
            marketplace=marketplace.value,
            reason=note_text,
            context={"fulfillment": fulfillment.strip()},
        )
        if denied is not None:
            return denied
    if not preview_requested and not confirmation_token.strip():
        return JSONResponse(
            {"ok": False, "error": "Сначала проверьте количество и подтвердите внесение"},
            status_code=400,
        )

    file_bytes = await file.read() if (file is not None and file.filename) else None
    source_type, _ = _source_of(file, file_bytes, sheet_url)

    try:
        if file_bytes is not None:
            report = await run_in_threadpool(
                ff_stock_import.import_ff_stock_from_xlsx,
                slug.lower(),
                fulfillment,
                file_bytes,
                file.filename,
                marketplace.value,
                preview=preview_requested,
                confirmation_token=confirmation_token.strip() or None,
            )
        elif sheet_url.strip():
            report = await run_in_threadpool(
                ff_stock_import.import_ff_stock_from_sheet,
                slug.lower(),
                fulfillment,
                sheet_url.strip(),
                marketplace.value,
                preview=preview_requested,
                confirmation_token=confirmation_token.strip() or None,
            )
        else:
            return JSONResponse(
                {"ok": False, "error": "Прикрепите файл .xlsx или вставьте ссылку на Google Таблицу"},
                status_code=400,
            )
    except ff_stock_import.FFImportConfirmationError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=409)
    except ff_stock_import.FFImportError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception:
        logger.exception("Загрузка остатков ФФ (%s, %s) упала с ошибкой", slug, fulfillment)
        return JSONResponse(
            {"ok": False, "error": "непредвиденная ошибка при обработке файла/таблицы — см. лог сервера"},
            status_code=500,
        )

    if preview_requested:
        return JSONResponse({"ok": True, "preview": report})

    actor = request.state.user
    def _record() -> None:
        now = _now_iso()
        operation_id = db.record_operation(
            store_slug=slug.lower(),
            kind="delivery",
            source_type=source_type,
            items=report.get("items", []),
            user_id=actor["id"],
            user_name=actor["full_name"],
            created_at=now,
            source_name=report.get("table_title"),
            sheet_url=sheet_url.strip() or None,
            to_fulfillment=fulfillment,
            to_marketplace=marketplace.value,
            note=note_text,
        )
        db.log_action_for_operation(
            actor["id"],
            actor["full_name"],
            "Загружена поставка на ФФ",
            f"{store.name} · {marketplace.value} · {fulfillment} · «{report['table_title']}» — "
            f"добавлено {report.get('added_quantity', 0)} шт. в "
            f"{report.get('applied', len(report.get('items', [])))} позициях; "
            f"без изменений {len(report.get('unchanged', []))}" + (f" · {note_text}" if note_text else ""),
            now,
            operation_id,
        )

    await run_in_threadpool(_record)
    return JSONResponse({"ok": True, "report": report})


@router.get("/stock/{slug}/catalog-search")
async def catalog_search(request: Request, slug: str, q: str = "", ff: str = "", mp: str = ""):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    marketplace = mp or (accessible_marketplaces(request.state.user, slug.lower()) or ("",))[0]
    if not marketplace or not has_action_permission(
        request.state.user,
        ActionPermission.STOCK_BALANCE_VIEW,
        store_slug=slug.lower(),
        marketplace=marketplace,
    ):
        raise HTTPException(status_code=403, detail="Нет доступа к этой площадке")
    items = await run_in_threadpool(
        db.search_catalog,
        slug.lower(),
        q,
        15,
        ff or None,
        marketplace,
    )
    return JSONResponse({"items": items})


@router.post("/stock/{slug}/add-ff-items")
async def add_ff_items(
    request: Request,
    slug: str,
    payload: AddFulfillmentItemsRequest,
    stock: StockMovementServiceDependency,
):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    note_text, note_error = _required_note(payload.note)
    if note_error is not None:
        return note_error
    if not payload.confirmed:
        return JSONResponse(
            {"ok": False, "error": "Подтвердите итоговое количество перед внесением"},
            status_code=400,
        )
    denied = await run_in_threadpool(
        _guard_stock_action,
        request.state.user,
        permission=ActionPermission.STOCK_RECEIVE,
        store_slug=slug.lower(),
        marketplace=payload.marketplace.value,
        reason=note_text,
        context={
            "fulfillment": payload.fulfillment,
            "quantity": sum(item.quantity for item in payload.items),
        },
    )
    if denied is not None:
        return denied

    try:
        results = await run_in_threadpool(
            stock.add_items,
            AddFulfillmentItemsCommand(store_slug=slug.lower(), request=payload),
        )
    except (ff_stock_import.FFImportError, StockValidationError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception:
        logger.exception("Ручная докладка на ФФ (%s, %s) упала", slug, payload.fulfillment)
        return JSONResponse(
            {"ok": False, "error": "непредвиденная ошибка — см. лог сервера"}, status_code=500
        )

    actor = request.state.user
    details = ", ".join(f"{item.article} +{item.added}" for item in results.root)
    def _record() -> None:
        now = _now_iso()
        operation_id = db.record_operation(
            store_slug=slug.lower(),
            kind="manual_add",
            source_type="manual",
            items=[
                {
                    "article": item.article,
                    "barcode": item.barcode,
                    "name": item.name,
                    "quantity": item.added,
                }
                for item in results.root
            ],
            user_id=actor["id"],
            user_name=actor["full_name"],
            created_at=now,
            to_fulfillment=payload.fulfillment,
            to_marketplace=payload.marketplace.value,
            note=note_text,
        )
        db.log_action_for_operation(
            actor["id"],
            actor["full_name"],
            "Добавлен остаток на ФФ вручную",
            f"{store.name} · {payload.fulfillment} · {details}" + (f" · {note_text}" if note_text else ""),
            now,
            operation_id,
        )

    await run_in_threadpool(_record)

    return JSONResponse({"ok": True, "results": results.model_dump(mode="json")})


@router.get("/stock/{slug}/ff-cell")
async def ff_cell_stock(request: Request, slug: str, ff: str = "", mp: str = ""):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if not ff or not mp:
        return JSONResponse({"stock": {}})
    if not has_action_permission(
        request.state.user,
        ActionPermission.STOCK_BALANCE_VIEW,
        store_slug=slug.lower(),
        marketplace=mp,
    ):
        raise HTTPException(status_code=403, detail="Нет доступа к этой площадке")
    stock = await run_in_threadpool(db.get_ff_available_totals, slug.lower(), ff, mp)
    return JSONResponse({"stock": stock})


def _guard_stock_edit(actor: dict) -> str | None:

    if auth.can_edit_stock(actor):
        return None
    return "изменение остатков для вашей учётной записи пока закрыто — обратитесь к администратору"


def _source_of(file: UploadFile | None, file_bytes: bytes | None, sheet_url: str) -> tuple[str, str | None]:

    if file is not None and file.filename:
        return "file", file.filename
    if sheet_url.strip():
        return "sheet", None
    return "manual", None


def _guard_used_source(
    store_slug: str, kind: str, source_type: str, sheet_url: str, file_bytes: bytes | None, label: str
) -> tuple[str | None, str | None]:

    fingerprint = db.source_fingerprint(source_type, sheet_url.strip() or None, file_bytes)
    used = db.find_used_source(store_slug, kind, fingerprint)
    if used is None:
        return fingerprint, None

    what = "Эта ссылка" if source_type == "sheet" else "Этот файл"
    return fingerprint, (
        f"{what} уже проводили {format_dt(used['created_at'])}"
        + (f" — {used['user_name']}" if used.get("user_name") else "")
        + ". Повторное проведение запрещено, чтобы не начислить или не списать дважды."
        + (
            f" Тогда источник назывался «{used['label']}»."
            if used.get("label") and used["label"] != label
            else ""
        )
    )


@router.post("/stock/{slug}/transfer")
async def transfer_ff_stock(
    request: Request,
    slug: str,
    stock: StockMovementServiceDependency,
    from_fulfillment: str = Form(...),
    from_marketplace: Marketplace = Form(...),
    to_fulfillment: str = Form(...),
    to_marketplace: Marketplace = Form(...),
    note: str = Form("", max_length=200),
    items: str = Form(""),
    sheet_url: str = Form(""),
    file: UploadFile | None = File(None),
):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    actor = request.state.user
    note_text, note_error = _required_note(note)
    if note_error is not None:
        return note_error
    denied = await run_in_threadpool(
        _guard_stock_action,
        actor,
        permission=ActionPermission.STOCK_TRANSFER,
        store_slug=slug.lower(),
        marketplace=from_marketplace.value,
        reason=note_text,
        context={
            "from_fulfillment": from_fulfillment,
            "to_fulfillment": to_fulfillment,
            "to_marketplace": to_marketplace.value,
        },
    )
    if denied is not None:
        return denied
    if from_marketplace is not to_marketplace:
        denied = await run_in_threadpool(
            _guard_stock_action,
            actor,
            permission=ActionPermission.STOCK_TRANSFER_CROSS_MARKETPLACE,
            store_slug=slug.lower(),
            marketplace=from_marketplace.value,
            target_marketplace=to_marketplace.value,
            reason=note_text,
            context={
                "from_fulfillment": from_fulfillment,
                "to_fulfillment": to_fulfillment,
            },
        )
        if denied is not None:
            return denied

    file_bytes = await file.read() if (file is not None and file.filename) else None
    source_type, source_name = _source_of(file, file_bytes, sheet_url)
    label = source_name or sheet_url.strip() or "ручной ввод"

    source_kind = f"transfer:{from_marketplace.value}"
    fingerprint, used_error = await run_in_threadpool(
        _guard_used_source,
        slug.lower(),
        source_kind,
        source_type,
        sheet_url,
        file_bytes,
        label,
    )
    if used_error:
        return JSONResponse({"ok": False, "error": used_error}, status_code=400)

    try:
        if file_bytes is not None:
            raw_entries = await run_in_threadpool(ff_transfer.entries_from_xlsx, file_bytes)
        elif sheet_url.strip():
            raw_entries = await run_in_threadpool(ff_transfer.entries_from_sheet, sheet_url.strip())
        else:
            try:
                raw_entries = SignedStockEntries.model_validate_json(items or "[]")
            except ValidationError:
                return JSONResponse({"ok": False, "error": "неверный формат позиций"}, status_code=400)

        transfer_result = await run_in_threadpool(
            stock.transfer,
            TransferStockCommand(
                store_slug=slug.lower(),
                entries=raw_entries,
                from_fulfillment=from_fulfillment,
                from_marketplace=from_marketplace,
                to_fulfillment=to_fulfillment,
                to_marketplace=to_marketplace,
                user_id=actor.id,
                user_name=actor.full_name,
                note=note_text,
            ),
        )
    except (ff_stock_import.FFImportError, StockValidationError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception:
        logger.exception("Перемещение остатков (%s) упало", slug)
        return JSONResponse(
            {"ok": False, "error": "непредвиденная ошибка — см. лог сервера"}, status_code=500
        )

    results = transfer_result.moved
    skipped = transfer_result.skipped
    if transfer_result.transfer_id is None:
        return JSONResponse(
            {"ok": False, "error": "перемещение создано без номера партии"},
            status_code=500,
        )
    transit_batch = await run_in_threadpool(db.get_ff_transit_batch, transfer_result.transfer_id)
    if transit_batch is None:
        return JSONResponse(
            {"ok": False, "error": "не удалось прочитать созданную партию перемещения"},
            status_code=500,
        )
    moved = ", ".join(f"{item.article} x{item.quantity}" for item in results.root)
    skipped_note = "; ".join(f"{item.article} x{item.quantity}: {item.reason}" for item in skipped.root)
    operation_note = " · ".join(
        part for part in (note_text, f"Не переведено: {skipped_note}" if skipped_note else "") if part
    )

    def _record() -> None:
        now = _now_iso()
        operation_id = db.record_operation(
            store_slug=slug.lower(),
            kind="transfer_dispatch",
            source_type=source_type,
            items=[
                {
                    "article": item["to_article"],
                    "barcode": item.get("barcode"),
                    "name": item.get("name"),
                    "quantity": item["sent_quantity"],
                    "purchase_price": item.get("purchase_price"),
                }
                for item in transit_batch["items"]
            ],
            user_id=actor.id,
            user_name=actor.full_name,
            created_at=now,
            source_name=source_name,
            sheet_url=sheet_url.strip() or None,
            from_fulfillment=from_fulfillment.strip(),
            from_marketplace=from_marketplace.value,
            to_fulfillment=to_fulfillment.strip(),
            to_marketplace=to_marketplace.value,
            note=operation_note or None,
            transit_batch_id=transfer_result.transfer_id,
        )
        db.log_action_for_operation(
            actor.id,
            actor.full_name,
            "Отправлено перемещение между фулфилментами",
            f"{store.name} · {from_fulfillment}/{from_marketplace.value} -> "
            f"{to_fulfillment}/{to_marketplace.value} · {moved}"
            + (f" · {note_text}" if note_text else "")
            + (f" · не переведено: {skipped_note}" if skipped_note else ""),
            now,
            operation_id,
        )

        db.record_used_source(
            slug.lower(),
            source_kind,
            fingerprint,
            label,
            source_type,
            operation_id,
            actor.full_name,
            now,
        )

    await run_in_threadpool(_record)

    return JSONResponse(
        {
            "ok": True,
            "transfer_id": transfer_result.transfer_id,
            "status": "in_transit",
            "results": results.model_dump(mode="json"),
            "skipped": skipped.model_dump(mode="json"),
        }
    )


@router.get("/stock/{slug}/transfers/in-transit")
async def list_in_transit_transfers(
    request: Request,
    slug: str,
    mp: str = "",
    view: str = "active",
):
    store_slug = slug.lower()
    if store_slug not in STORES:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    marketplace = mp.strip() or Marketplace.WB.value
    if marketplace not in {item.value for item in Marketplace}:
        raise HTTPException(status_code=400, detail="Неизвестный маркетплейс")
    if view not in {"active", "history"}:
        raise HTTPException(status_code=400, detail="Неизвестный вид перемещений")
    if not has_action_permission(
        request.state.user,
        ActionPermission.STOCK_BALANCE_VIEW,
        store_slug=store_slug,
        marketplace=marketplace,
    ):
        raise HTTPException(status_code=403, detail="Нет доступа к этой площадке")

    batches = await run_in_threadpool(
        db.get_ff_transit_batches,
        store_slug,
        marketplace,
        active_only=view == "active",
        closed_only=view == "history",
    )
    for batch in batches:
        batch["can_receive"] = view == "active" and has_action_permission(
            request.state.user,
            ActionPermission.STOCK_TRANSFER_RECEIVE,
            store_slug=store_slug,
            marketplace=batch["to_marketplace"],
        )
        batch["can_cancel"] = view == "active" and has_action_permission(
            request.state.user,
            ActionPermission.STOCK_TRANSFER_CANCEL,
            store_slug=store_slug,
            marketplace=batch["from_marketplace"],
        )
        batch["can_reopen"] = (
            batch["status"] in {"received", "partial"}
            and int(batch.get("received_units") or 0) > 0
            and int(batch.get("cancelled_units") or 0) == 0
            and has_action_permission(
                request.state.user,
                ActionPermission.STOCK_TRANSFER_CANCEL,
                store_slug=store_slug,
                marketplace=batch["to_marketplace"],
            )
        )
    return JSONResponse({"ok": True, "view": view, "batches": batches})


@router.post("/stock/{slug}/transfers/{transfer_id}/receive")
async def receive_in_transit_transfer(
    request: Request,
    slug: str,
    transfer_id: int,
    payload: ReceiveTransitRequest,
    stock: StockMovementServiceDependency,
):
    store_slug = slug.lower()
    batch = await run_in_threadpool(db.get_ff_transit_batch, transfer_id)
    if batch is None or batch["store_slug"] != store_slug:
        return JSONResponse({"ok": False, "error": "Перемещение не найдено"}, status_code=404)
    actor = request.state.user
    if not has_action_permission(
        actor,
        ActionPermission.STOCK_TRANSFER_RECEIVE,
        store_slug=store_slug,
        marketplace=batch["to_marketplace"],
    ):
        return JSONResponse(
            {"ok": False, "error": "Нет доступа к приёмке на площадке назначения"},
            status_code=403,
        )

    try:
        result = await run_in_threadpool(
            stock.receive_transfer,
            ReceiveTransitCommand(
                transfer_id=transfer_id,
                request=payload,
                user_id=actor.id,
                user_name=actor.full_name,
            ),
        )
    except StockValidationError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)

    now = _now_iso()
    items = result.moved.model_dump(mode="json")
    operation_id = await run_in_threadpool(
        db.record_operation,
        store_slug,
        "transfer_receive",
        "manual",
        items,
        actor.id,
        actor.full_name,
        now,
        from_fulfillment=f"В пути №{transfer_id}",
        from_marketplace=batch["from_marketplace"],
        to_fulfillment=batch["to_fulfillment"],
        to_marketplace=batch["to_marketplace"],
        note=payload.note.strip() or None,
        transit_batch_id=transfer_id,
    )
    moved = ", ".join(f"{item.article} x{item.quantity}" for item in result.moved.root)
    await run_in_threadpool(
        db.log_action_for_operation,
        actor.id,
        actor.full_name,
        "Принято перемещение между фулфилментами",
        f"{STORES[store_slug]['name']} · партия №{transfer_id} · {moved}",
        now,
        operation_id,
    )
    return JSONResponse(
        {
            "ok": True,
            "transfer_id": transfer_id,
            "status": result.status,
            "results": items,
        }
    )


@router.post("/stock/{slug}/transfers/{transfer_id}/reopen")
async def reopen_received_transfer(
    request: Request,
    slug: str,
    transfer_id: int,
    payload: ReopenTransitRequest,
    stock: StockMovementServiceDependency,
):
    store_slug = slug.lower()
    batch = await run_in_threadpool(db.get_ff_transit_batch, transfer_id)
    if batch is None or batch["store_slug"] != store_slug:
        return JSONResponse({"ok": False, "error": "Перемещение не найдено"}, status_code=404)
    actor = request.state.user
    if not has_action_permission(
        actor,
        ActionPermission.STOCK_TRANSFER_CANCEL,
        store_slug=store_slug,
        marketplace=batch["to_marketplace"],
    ):
        return JSONResponse(
            {
                "ok": False,
                "error": "Возвращать приёмку в путь может старший менеджер или суперадминистратор",
            },
            status_code=403,
        )

    try:
        result = await run_in_threadpool(
            stock.reopen_transfer,
            ReopenTransitCommand(
                transfer_id=transfer_id,
                request=payload,
                user_id=actor.id,
                user_name=actor.full_name,
            ),
        )
    except StockValidationError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)

    now = _now_iso()
    moved_items = result.moved.model_dump(mode="json")
    operation_items = [item | {"quantity": -abs(int(item["quantity"]))} for item in moved_items]
    operation_id = await run_in_threadpool(
        db.record_operation,
        store_slug,
        "transfer_receive_revert",
        "manual",
        operation_items,
        actor.id,
        actor.full_name,
        now,
        from_fulfillment=batch["to_fulfillment"],
        from_marketplace=batch["to_marketplace"],
        to_fulfillment=f"В пути №{transfer_id}",
        to_marketplace=batch["to_marketplace"],
        note=payload.reason,
        transit_batch_id=transfer_id,
    )
    moved = ", ".join(f"{item.article} x{item.quantity}" for item in result.moved.root)
    await run_in_threadpool(
        db.log_action_for_operation,
        actor.id,
        actor.full_name,
        "Приёмка перемещения возвращена в путь",
        f"{STORES[store_slug]['name']} · партия №{transfer_id} · {moved} · {payload.reason}",
        now,
        operation_id,
    )
    return JSONResponse(
        {
            "ok": True,
            "transfer_id": transfer_id,
            "status": result.status,
            "results": moved_items,
        }
    )


@router.post("/stock/{slug}/transfers/{transfer_id}/cancel")
async def cancel_in_transit_transfer(
    request: Request,
    slug: str,
    transfer_id: int,
    payload: CancelTransitRequest,
    stock: StockMovementServiceDependency,
):
    store_slug = slug.lower()
    batch = await run_in_threadpool(db.get_ff_transit_batch, transfer_id)
    if batch is None or batch["store_slug"] != store_slug:
        return JSONResponse({"ok": False, "error": "Перемещение не найдено"}, status_code=404)
    actor = request.state.user
    if not has_action_permission(
        actor,
        ActionPermission.STOCK_TRANSFER_CANCEL,
        store_slug=store_slug,
        marketplace=batch["from_marketplace"],
    ):
        return JSONResponse(
            {"ok": False, "error": "Отменять перемещения может старший менеджер или суперадминистратор"},
            status_code=403,
        )

    try:
        result = await run_in_threadpool(
            stock.cancel_transfer,
            CancelTransitCommand(
                transfer_id=transfer_id,
                request=payload,
                user_id=actor.id,
                user_name=actor.full_name,
            ),
        )
    except StockValidationError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)

    now = _now_iso()
    items = result.moved.model_dump(mode="json")
    operation_id = await run_in_threadpool(
        db.record_operation,
        store_slug,
        "transfer_cancel",
        "manual",
        items,
        actor.id,
        actor.full_name,
        now,
        from_fulfillment=f"В пути №{transfer_id}",
        from_marketplace=batch["to_marketplace"],
        to_fulfillment=batch["from_fulfillment"],
        to_marketplace=batch["from_marketplace"],
        note=payload.reason,
        transit_batch_id=transfer_id,
    )
    returned = ", ".join(f"{item.article} x{item.quantity}" for item in result.moved.root)
    await run_in_threadpool(
        db.log_action_for_operation,
        actor.id,
        actor.full_name,
        "Отменено перемещение между фулфилментами",
        f"{STORES[store_slug]['name']} · партия №{transfer_id} · возвращено: {returned}",
        now,
        operation_id,
    )
    return JSONResponse(
        {
            "ok": True,
            "transfer_id": transfer_id,
            "status": result.status,
            "results": items,
        }
    )


@router.post("/stock/{slug}/shipment")
async def ship_ff_stock(
    request: Request,
    slug: str,
    stock: StockMovementServiceDependency,
    fulfillment: str = Form(...),
    marketplace: Marketplace = Form(...),
    note: str = Form("", max_length=200),
    to_fbs: str = Form(""),
    to_trash: str = Form(""),
    items: str = Form(""),
    sheet_url: str = Form(""),
    file: UploadFile | None = File(None),
):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    actor = request.state.user
    note_text, note_error = _required_note(note)
    if note_error is not None:
        return note_error

    trash = to_trash.strip().lower() in ("1", "true", "on", "yes")
    fbs_transfer = to_fbs.strip().lower() in ("1", "true", "on", "yes")
    if trash and fbs_transfer:
        return JSONResponse(
            {"ok": False, "error": "Выберите только один тип операции: FBS или мусорка"},
            status_code=400,
        )
    kind = "trash" if trash else ("fbs_transfer" if fbs_transfer else "shipment")
    permission = ActionPermission.STOCK_WRITEOFF if trash else ActionPermission.STOCK_SHIPMENT
    denied = await run_in_threadpool(
        _guard_stock_action,
        actor,
        permission=permission,
        store_slug=slug.lower(),
        marketplace=marketplace.value,
        reason=note_text,
        context={"fulfillment": fulfillment, "kind": kind},
    )
    if denied is not None:
        return denied

    source_kind = f"{kind}:{marketplace.value}"

    file_bytes = await file.read() if (file is not None and file.filename) else None
    source_type, source_name = _source_of(file, file_bytes, sheet_url)
    label = source_name or sheet_url.strip() or "ручной ввод"

    fingerprint, used_error = await run_in_threadpool(
        _guard_used_source,
        slug.lower(),
        source_kind,
        source_type,
        sheet_url,
        file_bytes,
        label,
    )
    if used_error:
        return JSONResponse({"ok": False, "error": used_error}, status_code=400)

    try:
        if file_bytes is not None:
            raw_entries = await run_in_threadpool(ff_shipment.entries_from_xlsx, file_bytes)
        elif sheet_url.strip():
            raw_entries = await run_in_threadpool(ff_shipment.entries_from_sheet, sheet_url.strip())
        else:
            try:
                raw_entries = SignedStockEntries.model_validate_json(items or "[]")
            except ValidationError:
                return JSONResponse({"ok": False, "error": "неверный формат позиций"}, status_code=400)

        command = ShipmentCommand(
            store_slug=slug.lower(),
            entries=raw_entries,
            fulfillment=fulfillment,
            marketplace=marketplace,
            to_trash=trash,
        )
        results = await run_in_threadpool(
            stock.register_fbs_transfer if fbs_transfer else stock.ship,
            command,
        )
    except (ff_stock_import.FFImportError, StockValidationError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception:
        logger.exception("Отгрузка стока (%s) упала", slug)
        return JSONResponse(
            {"ok": False, "error": "непредвиденная ошибка — см. лог сервера"}, status_code=500
        )

    shipped = ", ".join(f"{item.article} x{item.quantity}" for item in results.root)
    def _record() -> None:
        now = _now_iso()
        operation_id = db.record_operation(
            store_slug=slug.lower(),
            kind=kind,
            source_type=source_type,
            items=results.model_dump(mode="json"),
            user_id=actor.id,
            user_name=actor.full_name,
            created_at=now,
            source_name=source_name,
            sheet_url=sheet_url.strip() or None,
            from_fulfillment=fulfillment.strip(),
            from_marketplace=marketplace.value,
            to_fulfillment="Мусорка" if trash else ("FBS" if fbs_transfer else None),
            to_marketplace=marketplace.value if (trash or fbs_transfer) else None,
            note=note_text,
        )
        db.log_action_for_operation(
            actor.id,
            actor.full_name,
            (
                "Списание в мусорку"
                if trash
                else ("Перемещение на FBS" if fbs_transfer else "Отгрузка стока")
            ),
            f"{store.name} · {fulfillment}/{marketplace.value} · {shipped}"
            + (f" · {note_text}" if note_text else ""),
            now,
            operation_id,
        )
        db.record_used_source(
            slug.lower(),
            source_kind,
            fingerprint,
            label,
            source_type,
            operation_id,
            actor.full_name,
            now,
        )

    await run_in_threadpool(_record)

    return JSONResponse({"ok": True, "results": results.model_dump(mode="json")})
