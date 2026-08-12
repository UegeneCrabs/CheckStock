import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app import auth, db
from app.dto.marketplace import Marketplace
from app.dto.stock import (
    AddFulfillmentItemsCommand,
    AddFulfillmentItemsRequest,
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
from app.web.common import _now_iso
from app.web.dependencies import StockMovementServiceDependency

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/stock/{slug}/upload-ff-stock")
async def upload_ff_stock(
    request: Request,
    slug: str,
    fulfillment: str = Form(...),
    marketplace: Marketplace = Form(Marketplace.WB),
    sheet_url: str = Form(""),
    file: UploadFile | None = File(None),
):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    denied = _guard_stock_edit(request.state.user)
    if denied:
        return JSONResponse({"ok": False, "error": denied}, status_code=403)

    fulfillment = fulfillment.strip()
    if not fulfillment:
        return JSONResponse({"ok": False, "error": "Выберите фулфилмент назначения"}, status_code=400)

    file_bytes = await file.read() if (file is not None and file.filename) else None
    source_type, source_name = _source_of(file, file_bytes, sheet_url)
    label = source_name or sheet_url.strip() or "ручной ввод"

    source_kind = f"delivery:{marketplace.value}"
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
            report = await run_in_threadpool(
                ff_stock_import.import_ff_stock_from_xlsx,
                slug.lower(),
                fulfillment,
                file_bytes,
                file.filename,
                marketplace.value,
            )
        elif sheet_url.strip():
            report = await run_in_threadpool(
                ff_stock_import.import_ff_stock_from_sheet,
                slug.lower(),
                fulfillment,
                sheet_url.strip(),
                marketplace.value,
            )
        else:
            return JSONResponse(
                {"ok": False, "error": "Прикрепите файл .xlsx или вставьте ссылку на Google Таблицу"},
                status_code=400,
            )
    except ff_stock_import.FFImportError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception:
        logger.exception("Загрузка остатков ФФ (%s, %s) упала с ошибкой", slug, fulfillment)
        return JSONResponse(
            {"ok": False, "error": "непредвиденная ошибка при обработке файла/таблицы — см. лог сервера"},
            status_code=500,
        )

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
        )
        db.log_action_for_operation(
            actor["id"],
            actor["full_name"],
            "Загружена поставка на ФФ",
            f"{store.name} · {marketplace.value} · {fulfillment} · «{report['table_title']}» — "
            f"обновлено {report['matched']} из {report['total_rows']} строк",
            now,
            operation_id,
        )
        db.record_used_source(
            slug.lower(),
            source_kind,
            fingerprint,
            report.get("table_title") or label,
            source_type,
            operation_id,
            actor["full_name"],
            now,
        )

    await run_in_threadpool(_record)
    return JSONResponse({"ok": True, "report": report})


@router.get("/stock/{slug}/catalog-search")
async def catalog_search(slug: str, q: str = "", ff: str = "", mp: str = ""):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    items = await run_in_threadpool(db.search_catalog, slug.lower(), q, 15, ff or None, mp or None)
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

    denied = _guard_stock_edit(request.state.user)
    if denied:
        return JSONResponse({"ok": False, "error": denied}, status_code=403)

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
        )
        db.log_action_for_operation(
            actor["id"],
            actor["full_name"],
            "Добавлен остаток на ФФ вручную",
            f"{store.name} · {payload.fulfillment} · {details}",
            now,
            operation_id,
        )

    await run_in_threadpool(_record)

    return JSONResponse({"ok": True, "results": results.model_dump(mode="json")})


@router.get("/stock/{slug}/ff-cell")
async def ff_cell_stock(slug: str, ff: str = "", mp: str = ""):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if not ff or not mp:
        return JSONResponse({"stock": {}})
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
    items: str = Form(""),
    sheet_url: str = Form(""),
    file: UploadFile | None = File(None),
):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    actor = request.state.user
    denied = _guard_stock_edit(actor)
    if denied:
        return JSONResponse({"ok": False, "error": denied}, status_code=403)

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
    moved = ", ".join(f"{item.article} x{item.quantity}" for item in results.root)
    skipped_note = "; ".join(f"{item.article} x{item.quantity}: {item.reason}" for item in skipped.root)

    def _record() -> None:
        now = _now_iso()
        operation_id = db.record_operation(
            store_slug=slug.lower(),
            kind="transfer",
            source_type=source_type,
            items=results.model_dump(mode="json"),
            user_id=actor.id,
            user_name=actor.full_name,
            created_at=now,
            source_name=source_name,
            sheet_url=sheet_url.strip() or None,
            from_fulfillment=from_fulfillment.strip(),
            from_marketplace=from_marketplace.value,
            to_fulfillment=to_fulfillment.strip(),
            to_marketplace=to_marketplace.value,
            note=(f"Не переведено: {skipped_note}" if skipped_note else None),
        )
        db.log_action_for_operation(
            actor.id,
            actor.full_name,
            "Перемещение между фулфилментами",
            f"{store.name} · {from_fulfillment}/{from_marketplace.value} -> "
            f"{to_fulfillment}/{to_marketplace.value} · {moved}"
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
            "results": results.model_dump(mode="json"),
            "skipped": skipped.model_dump(mode="json"),
        }
    )


@router.post("/stock/{slug}/shipment")
async def ship_ff_stock(
    request: Request,
    slug: str,
    stock: StockMovementServiceDependency,
    fulfillment: str = Form(...),
    marketplace: Marketplace = Form(...),
    note: str = Form(""),
    to_trash: str = Form(""),
    items: str = Form(""),
    sheet_url: str = Form(""),
    file: UploadFile | None = File(None),
):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    actor = request.state.user
    denied = _guard_stock_edit(actor)
    if denied:
        return JSONResponse({"ok": False, "error": denied}, status_code=403)

    trash = to_trash.strip().lower() in ("1", "true", "on", "yes")
    kind = "trash" if trash else "shipment"

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

        results = await run_in_threadpool(
            stock.ship,
            ShipmentCommand(
                store_slug=slug.lower(),
                entries=raw_entries,
                fulfillment=fulfillment,
                marketplace=marketplace,
                to_trash=trash,
            ),
        )
    except (ff_stock_import.FFImportError, StockValidationError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception:
        logger.exception("Отгрузка стока (%s) упала", slug)
        return JSONResponse(
            {"ok": False, "error": "непредвиденная ошибка — см. лог сервера"}, status_code=500
        )

    shipped = ", ".join(f"{item.article} x{item.quantity}" for item in results.root)
    note_text = note.strip()

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
            to_fulfillment="Мусорка" if trash else None,
            to_marketplace=marketplace.value if trash else None,
            note=note_text or None,
        )
        db.log_action_for_operation(
            actor.id,
            actor.full_name,
            "Списание в мусорку" if trash else "Отгрузка стока",
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
