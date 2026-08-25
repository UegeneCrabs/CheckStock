import html
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import auth, db, stock_cost_report, stock_cost_report_export
from app.domain import MARKETPLACES, MOSCOW_TIMEZONE
from app.stores import STORES
from app.web.access import accessible_store_slugs
from app.web.common import _fmt_num, _now_iso
from app.web.downloads import _download_headers
from app.web.routers.stock_mutations import _guard_stock_edit
from app.web.templating import fill_template, render_page

router = APIRouter()

VIEW_LABELS = (
    ("summary", "Сводная"),
    ("deliveries", "Поставки"),
    ("transfers", "Перемещения"),
    ("shipments", "Отгрузки"),
    ("fbs_sales", "Продажи FBS"),
)


def _default_period(today: date | None = None) -> tuple[date, date]:
    current = today or datetime.now(MOSCOW_TIMEZONE).date()
    return current - timedelta(days=current.weekday()), current


def _period(date_from: str, date_to: str) -> tuple[date, date]:
    default_from, default_to = _default_period()
    try:
        start = date.fromisoformat(date_from) if date_from else default_from
        end = date.fromisoformat(date_to) if date_to else default_to
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Некорректный период отчёта") from error
    if end < start:
        raise HTTPException(status_code=400, detail="Конец периода не может быть раньше начала")
    if (end - start).days > 366:
        raise HTTPException(status_code=400, detail="Период отчёта не может быть больше 366 дней")
    return start, end


def _money(value: float) -> str:
    formatted = f"{float(value):,.2f}".replace(",", " ").replace(".00", "")
    return f"{formatted} ₽"


def _metric_cell(metric: dict) -> str:
    return (
        '<span class="cost-metric">'
        f"<strong>{html.escape(_money(float(metric.get('cost') or 0)))}</strong>"
        f"<small>{_fmt_num(int(metric.get('units') or 0))} ед.</small></span>"
    )


def _fbs_units_cell(metric: dict, formula_note: str) -> str:
    units = _fmt_num(int(metric.get("units") or 0))
    return f'<span class="cost-metric"><strong>{units} ед.</strong>{formula_note}</span>'


def _fbs_cost_cell(metric: dict) -> str:
    return (
        '<span class="cost-metric">'
        f"<strong>{html.escape(_money(float(metric.get('cost') or 0)))}</strong></span>"
    )


def _summary_table(rows: list[dict]) -> str:
    body = []
    for row in rows:
        formula = row.get("fbs_formula") or {}
        if formula.get("available"):
            formula_metric = _metric_cell(formula["metric"])
            formula_note = (
                '<span class="formula-note">по остаткам: '
                f"{_fmt_num(formula['start_units'])} + {_fmt_num(formula['moved_units'])} − "
                f"{_fmt_num(formula['end_units'])}</span>"
            )
        else:
            formula_metric = '<span class="snapshot-wait">Снимки границ ещё не накоплены</span>'
            formula_note = ""
        body.append(
            "<tr>"
            f"<th>{html.escape(row['store_name'])}</th>"
            f"<td>{html.escape(row['marketplace'])}</td>"
            f"<td>{_metric_cell(row['deliveries'])}</td>"
            f"<td>{_metric_cell(row['moved_out'])}</td>"
            f"<td>{_metric_cell(row['moved_in'])}</td>"
            f"<td>{_metric_cell(row['shipped'])}</td>"
            f"<td>{_fbs_units_cell(row['fbs_sales'], formula_note)}</td>"
            f"<td>{_fbs_cost_cell(row['fbs_sales'])}</td>"
            f"<td>{formula_metric}</td>"
            "</tr>"
        )
    return (
        '<div class="cost-table-wrap table-wrap"><table class="cost-summary-table data-table"><thead><tr>'
        "<th>Магазин</th><th>Маркетплейс</th><th>Поставки</th><th>Со стока</th><th>На сток</th>"
        "<th>Отгружено наружу</th><th>Продано FBS, ед.</th><th>ЗЦ продаж FBS</th>"
        "<th>Сверка по остаткам</th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>"
    )


def _operation_cost(operation: dict) -> str:
    return f'<span class="operation-cost"><strong>{_money(operation["purchase_cost"])}</strong></span>'


def _classification_form(operation: dict, query: dict[str, str], can_edit: bool) -> str:
    if operation.get("kind") != "shipment":
        return "—"
    active = bool(operation.get("is_fbs_transfer"))
    if not can_edit:
        return "На FBS" if active else "Наружу"
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(key)}" value="{html.escape(value, quote=True)}">'
        for key, value in query.items()
    )
    return (
        f'<form class="fbs-classifier" method="post" '
        f'action="/stock/cost-report/operations/{int(operation["id"])}/fbs-transfer">'
        f"{hidden}"
        f'<input type="hidden" name="is_fbs_transfer" value="{0 if active else 1}">'
        f'<button class="{"is-active" if active else ""}" type="submit">'
        f"{'На FBS' if active else 'Отгрузка наружу'}</button></form>"
    )


def _operation_marketplace(operation: dict) -> str:
    source = str(operation.get("from_marketplace") or "")
    target = str(operation.get("to_marketplace") or "")
    if source and target and source != target:
        return f"{source} → {target}"
    return target or source or "—"


def _operation_table(
    operations: list[dict],
    query: dict[str, str],
    can_edit: bool,
) -> str:
    if not operations:
        return '<div class="cost-empty">За выбранный период таких операций нет.</div>'
    rows = []
    for operation in operations:
        created = str(operation.get("created_at") or "").replace("T", " ")[:16]
        rows.append(
            "<tr>"
            f"<td>{html.escape(created)}</td>"
            f"<td>{html.escape(STORES[operation['store_slug']]['name'])}</td>"
            f"<td>{html.escape(_operation_marketplace(operation))}</td>"
            f"<td>{html.escape(operation['label'])}</td>"
            f"<td><span>{html.escape(operation['from_label'])}</span><b>→</b>"
            f"<span>{html.escape(operation['to_label'])}</span></td>"
            f'<td data-filter-value="{int(operation["units"])}"><strong>{_fmt_num(operation["units"])} ед.</strong>'
            f"<small>{_fmt_num(operation['positions'])} поз.</small></td>"
            f"<td>{_operation_cost(operation)}</td>"
            f"<td>{_classification_form(operation, query, can_edit)}</td>"
            "</tr>"
        )
    return (
        '<div class="cost-table-wrap table-wrap"><table class="cost-operation-table data-table"><thead><tr>'
        "<th>Когда</th><th>Магазин</th><th>Маркетплейс</th><th>Операция</th><th>Маршрут</th>"
        '<th data-filter-type="number">Количество</th><th>ЗЦ</th>'
        "<th>Учёт отгрузки</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _fbs_sales_table(report: dict) -> str:
    if not report["fbs_sales"]:
        return '<div class="cost-empty">Продаж FBS за выбранный период в загруженных данных нет.</div>'
    formula = {
        (entry["store_slug"], entry["marketplace"], item["article"]): item
        for entry in report["reconciliation"]
        if entry["available"]
        for item in entry["items"]
    }
    rows = []
    for item in report["fbs_sales"]:
        check = formula.get((item["store_slug"], item["marketplace"], item["article"]))
        check_html = (
            f"{_fmt_num(check['start_quantity'])} + {_fmt_num(check['moved_quantity'])} − "
            f"{_fmt_num(check['end_quantity'])} = <strong>{_fmt_num(check['quantity'])}</strong>"
            if check
            else '<span class="snapshot-wait">ждём снимки границ</span>'
        )
        cost = _money(item["purchase_cost"]) if item["purchase_cost"] is not None else "нет ЗЦ"
        rows.append(
            "<tr>"
            f"<td>{html.escape(STORES[item['store_slug']]['name'])}</td>"
            f"<td>{html.escape(item['marketplace'])}</td>"
            f"<td><strong>{html.escape(str(item.get('name') or item['article']))}</strong>"
            f"<small>Арт. {html.escape(item['article'])} · {html.escape(item.get('barcode') or '—')}</small></td>"
            f"<td>{_fmt_num(item['quantity'])} ед.</td>"
            f"<td>{html.escape(_money(item['purchase_price'])) if item['purchase_price'] is not None else '—'}</td>"
            f"<td><strong>{html.escape(cost)}</strong></td>"
            f"<td>{check_html}</td>"
            "</tr>"
        )
    return (
        '<div class="cost-table-wrap table-wrap"><table class="cost-sales-table data-table"><thead><tr>'
        "<th>Магазин</th><th>Маркетплейс</th><th>Товар</th><th>Продано FBS</th><th>ЗЦ/ед.</th>"
        "<th>ЗЦ продаж</th><th>Сверка: начало + на FBS − конец</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _query(
    date_from: date,
    date_to: date,
    store: str,
    marketplace: str,
    view: str,
) -> dict[str, str]:
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "store": store,
        "marketplace": marketplace,
        "view": view,
    }


def _tabs(query: dict[str, str], active: str) -> str:
    return "".join(
        f'<a class="{"is-active" if view == active else ""}" '
        f'href="/stock/cost-report?{urlencode(query | {"view": view})}">{label}</a>'
        for view, label in VIEW_LABELS
    )


@router.get("/stock/cost-report", response_class=HTMLResponse)
async def stock_cost_report_page(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    store: str = "",
    marketplace: str = "",
    view: str = "summary",
):
    start, end = _period(date_from, date_to)
    allowed_stores = accessible_store_slugs(request.state.user)
    store = store if store in allowed_stores else ""
    store_slugs = (store,) if store else allowed_stores
    marketplace = marketplace if marketplace in MARKETPLACES else ""
    marketplaces = (marketplace,) if marketplace else tuple(MARKETPLACES)
    view = view if view in stock_cost_report.VIEW_KINDS else "summary"
    report = await run_in_threadpool(
        stock_cost_report.build_report,
        store_slugs,
        start,
        end,
        marketplaces,
    )
    query = _query(start, end, store, marketplace, view)
    current_monday, current_end = _default_period()
    previous_end = current_monday - timedelta(days=1)
    previous_start = previous_end - timedelta(days=6)
    current_week_url = "/stock/cost-report?" + urlencode(
        _query(current_monday, current_end, store, marketplace, view)
    )
    previous_week_url = "/stock/cost-report?" + urlencode(
        _query(previous_start, previous_end, store, marketplace, view)
    )
    store_options = '<option value="">Все магазины</option>' + "".join(
        f'<option value="{slug}"{" selected" if slug == store else ""}>'
        f"{html.escape(STORES[slug]['name'])}</option>"
        for slug in allowed_stores
    )
    marketplace_options = '<option value="">Все маркетплейсы</option>' + "".join(
        f'<option value="{html.escape(item, quote=True)}"'
        f"{' selected' if item == marketplace else ''}>{html.escape(item)}</option>"
        for item in MARKETPLACES
    )
    if view == "summary":
        detail = _summary_table(report["summary"])
    elif view == "fbs_sales":
        detail = _fbs_sales_table(report)
    else:
        detail = _operation_table(
            stock_cost_report.operations_for_view(report, view),
            query,
            auth.can_edit_stock(request.state.user),
        )
    export_url = "/stock/cost-report.xlsx?" + urlencode(query)
    content = fill_template(
        "stock_cost_report_content.html",
        date_from=start.isoformat(),
        date_to=end.isoformat(),
        store_options=store_options,
        marketplace_options=marketplace_options,
        current_week_url=html.escape(current_week_url, quote=True),
        previous_week_url=html.escape(previous_week_url, quote=True),
        export_url=html.escape(export_url, quote=True),
        tabs=_tabs(query, view),
        detail=detail,
    )
    return render_page(
        "РАКЕТА — Движение и ЗЦ",
        "stock_cost_report",
        content,
        request.state.user,
        content_class="content--stock-cost-report",
    )


@router.get("/stock/cost-report.xlsx")
async def stock_cost_report_xlsx(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    store: str = "",
    marketplace: str = "",
    view: str = "summary",
):
    start, end = _period(date_from, date_to)
    allowed_stores = accessible_store_slugs(request.state.user)
    store = store if store in allowed_stores else ""
    store_slugs = (store,) if store else allowed_stores
    marketplace = marketplace if marketplace in MARKETPLACES else ""
    marketplaces = (marketplace,) if marketplace else tuple(MARKETPLACES)
    view = view if view in stock_cost_report.VIEW_KINDS else "summary"
    report = await run_in_threadpool(
        stock_cost_report.build_report,
        store_slugs,
        start,
        end,
        marketplaces,
    )
    try:
        content, filename = await run_in_threadpool(stock_cost_report_export.build_xlsx, report, view)
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )


@router.post("/stock/cost-report/operations/{operation_id}/fbs-transfer")
async def classify_shipment_as_fbs_transfer(
    request: Request,
    operation_id: int,
    is_fbs_transfer: int = Form(0),
    date_from: str = Form(""),
    date_to: str = Form(""),
    store: str = Form(""),
    marketplace: str = Form(""),
    view: str = Form("shipments"),
):
    denied = _guard_stock_edit(request.state.user)
    if denied:
        raise HTTPException(status_code=403, detail=denied)
    operation = await run_in_threadpool(db.get_operation, operation_id)
    allowed_stores = accessible_store_slugs(request.state.user)
    if operation is None or operation.get("store_slug") not in allowed_stores:
        raise HTTPException(status_code=404, detail="Операция не найдена")
    changed = await run_in_threadpool(
        db.set_operation_fbs_transfer,
        operation_id,
        bool(is_fbs_transfer),
        request.state.user.get("id"),
        _now_iso(),
    )
    if not changed:
        raise HTTPException(status_code=400, detail="Пометить можно только отгрузку")
    redirect_query = {
        "date_from": date_from,
        "date_to": date_to,
        "store": store,
        "marketplace": marketplace,
        "view": view,
    }
    return RedirectResponse(
        "/stock/cost-report?" + urlencode(redirect_query),
        status_code=303,
    )
