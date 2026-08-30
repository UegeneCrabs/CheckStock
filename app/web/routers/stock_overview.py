import html
import math
import threading
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse

from app import db, health
from app.config import settings
from app.domain import MOSCOW_TIMEZONE
from app.formatting import format_dt
from app.repositories import stock_dashboard as stock_dashboard_repository
from app.stores import STORES
from app.web.access import accessible_store_slugs
from app.web.common import _fmt_num
from app.web.identifiers import copy_identifier
from app.web.templating import fill_template, render_page

router = APIRouter()


_stock2_cache_lock = threading.Lock()


_stock2_cache: dict[str, object] = {"rows": None, "expires_at": 0.0}


def _fmt_float(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "—"
    value = float(value)
    if abs(value) >= 1_000_000:
        return f"{_fmt_float(value / 1_000_000)} млн ₽"
    if abs(value) >= 1_000:
        return f"{_fmt_float(value / 1_000)} тыс ₽"
    return f"{_fmt_num(round(value))} ₽"


def _stock2_status(row: dict) -> tuple[str, str]:
    stock = int(row.get("total_stock") or 0)
    avg_daily = float(row.get("avg_daily") or 0)
    coverage = row.get("coverage_days")
    if stock <= 0:
        return "danger", "нет остатка"
    if not row.get("sales_loaded"):
        return "muted", "нет данных"
    if coverage is not None and coverage <= 7:
        return "danger", "закончится"
    if coverage is not None and coverage <= 14:
        return "warning", "пополнить"
    if coverage is not None and coverage >= settings.stock_excess_days:
        return "info", "избыток"
    if int(row.get("sold_60") or 0) <= 0:
        return "violet", "без движения"
    if avg_daily <= 0:
        return "warning", "нет продаж 30 дн."
    return "ok", "норма"


def _stock2_badge(tone: str, text: str) -> str:
    return f'<span class="stock2-badge stock2-badge--{tone}">{html.escape(text)}</span>'


def _stock2_product_cell(row: dict) -> str:
    article = str(row.get("article") or "")
    barcode = str(row.get("barcode") or "")
    meta = copy_identifier(article, "Артикул", f"Арт. {article}")
    if barcode:
        meta += f' <span aria-hidden="true">·</span> {copy_identifier(barcode, "Баркод")}'
    return (
        '<span class="stock2-product">'
        f'<strong title="{html.escape(str(row.get("name") or ""), quote=True)}">'
        f"{html.escape(str(row.get('name') or article or 'Без названия'))}</strong>"
        f"<small>{meta}</small>"
        "</span>"
    )


def _stock2_load_inventory_rows() -> list[dict]:
    now = datetime.now(MOSCOW_TIMEZONE)
    since_30 = (now - timedelta(days=settings.stock_window_days)).isoformat(timespec="seconds")
    since_60 = (now - timedelta(days=settings.stock_frozen_days)).isoformat(timespec="seconds")
    rows = stock_dashboard_repository.get_inventory_rows(since_30, since_60)
    result = []
    for row in rows:
        item = dict(row)
        store = STORES.get(item["store_slug"], {})
        total_stock = (
            int(item["marketplace_stock"] or 0)
            + int(item["fulfillment_stock"] or 0)
            + int(item.get("transit_stock") or 0)
        )
        sold_30 = int(item["sold_30"] or 0)
        avg_daily = sold_30 / settings.stock_window_days if sold_30 else 0
        coverage_days = total_stock / avg_daily if avg_daily > 0 else None
        purchase_price = item.get("purchase_price")
        item.update(
            {
                "store_name": store.get("name", item["store_slug"].upper()),
                "store_initials": store.get("initials", item["store_slug"][:2].upper()),
                "store_color": store.get("color", "#64748b"),
                "store_text": store.get("text", "#fff"),
                "in_catalog": item.get("catalog_id") is not None,
                "total_stock": total_stock,
                "avg_daily": avg_daily,
                "coverage_days": coverage_days,
                "stock_value": (float(purchase_price) * total_stock) if purchase_price is not None else None,
            }
        )
        result.append(item)
    return result


def _stock2_inventory_rows() -> list[dict]:
    now = time.monotonic()
    cached_rows = _stock2_cache.get("rows")
    if cached_rows is not None and now < float(_stock2_cache["expires_at"]):
        return cached_rows

    with _stock2_cache_lock:
        now = time.monotonic()
        cached_rows = _stock2_cache.get("rows")
        if cached_rows is not None and now < float(_stock2_cache["expires_at"]):
            return cached_rows
        rows = _stock2_load_inventory_rows()
        _stock2_cache["rows"] = rows
        _stock2_cache["expires_at"] = now + settings.stock_cache_ttl_seconds
        return rows


def _stock2_aggregate(rows: list[dict], key: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for row in rows:
        name = row[key]
        item = result.setdefault(
            name,
            {
                "sku_count": 0,
                "stock_sku_count": 0,
                "total_stock": 0,
                "marketplace_stock": 0,
                "fulfillment_stock": 0,
                "transit_stock": 0,
                "sold_30": 0,
                "avg_daily": 0.0,
                "stock_value": 0.0,
                "value_known": 0,
                "risk_count": 0,
                "zero_count": 0,
                "zero_selling_count": 0,
                "excess_count": 0,
                "frozen_count": 0,
                "no_data_count": 0,
                "coverage_stock": 0,
                "marketplaces": set(),
                "last_sync": None,
            },
        )
        total_stock = int(row["total_stock"])
        item["sku_count"] += 1 if row.get("in_catalog") else 0
        item["stock_sku_count"] += 1 if total_stock > 0 else 0
        item["total_stock"] += total_stock
        item["marketplace_stock"] += int(row["marketplace_stock"] or 0)
        item["fulfillment_stock"] += int(row["fulfillment_stock"] or 0)
        item["transit_stock"] += int(row.get("transit_stock") or 0)
        item["sold_30"] += int(row["sold_30"] or 0)
        item["avg_daily"] += float(row["avg_daily"] or 0)
        if row.get("stock_value") is not None:
            item["stock_value"] += float(row["stock_value"])
            item["value_known"] += 1
        if row.get("marketplace"):
            item["marketplaces"].add(row["marketplace"])
        if row.get("coverage_days") is not None and row["coverage_days"] <= 7 and total_stock > 0:
            item["risk_count"] += 1
        if total_stock <= 0:
            item["zero_count"] += 1
            if int(row.get("sold_30") or 0) > 0:
                item["zero_selling_count"] += 1
        if (
            total_stock > 0
            and row.get("coverage_days") is not None
            and row["coverage_days"] >= settings.stock_excess_days
        ):
            item["excess_count"] += 1
        if total_stock > 0 and row.get("sales_loaded") and int(row.get("sold_60") or 0) <= 0:
            item["frozen_count"] += 1
        if row.get("sales_loaded"):
            item["coverage_stock"] += total_stock
        else:
            item["no_data_count"] += 1
        if row.get("stock_updated_at"):
            item["last_sync"] = max(item["last_sync"] or row["stock_updated_at"], row["stock_updated_at"])

    for item in result.values():
        item["coverage_days"] = item["coverage_stock"] / item["avg_daily"] if item["avg_daily"] > 0 else None
    return result


def _stock2_aggregate_status(item: dict) -> tuple[str, str]:
    risk_count = int(item.get("risk_count") or 0)
    zero_selling = int(item.get("zero_selling_count") or 0)
    excess_count = int(item.get("excess_count") or 0)
    frozen_count = int(item.get("frozen_count") or 0)
    no_data_count = int(item.get("no_data_count") or 0)
    sku_count = int(item.get("sku_count") or 0)

    if sku_count and int(item.get("total_stock") or 0) <= 0:
        return "danger", "нет остатка"
    if risk_count:
        return "danger", f"{risk_count} срочно"
    if zero_selling:
        return "danger", f"{zero_selling} без остатка"
    if excess_count:
        return "info", f"{excess_count} избыток"
    if frozen_count:
        return "violet", f"{frozen_count} без движения"
    if no_data_count and no_data_count >= sku_count:
        return "muted", "нет данных продаж"
    if no_data_count:
        return "muted", "данные частично"
    return "ok", "норма"


def _stock2_summary_card(label: str, value: str, note: str, tone: str = "neutral") -> str:
    return (
        f'<article class="stock2-kpi stock2-kpi--{tone}">'
        f"<span>{html.escape(label)}</span>"
        f"<strong>{html.escape(value)}</strong>"
        f"<small>{html.escape(note)}</small>"
        "</article>"
    )


def _stock2_attention_card(title: str, value: str, note: str, tone: str, href: str, detail_label: str) -> str:
    return (
        f'<a class="stock2-attention-card stock2-attention-card--{tone}" href="{html.escape(href, quote=True)}">'
        f'<span class="stock2-attention-label">{html.escape(title)}</span>'
        f"<strong>{html.escape(value)}</strong>"
        f"<small>{html.escape(note)}</small>"
        f'<span class="stock2-card-link">{html.escape(detail_label)}</span>'
        "</a>"
    )


def _stock2_render_marketplaces(market_data: dict[str, dict]) -> str:
    labels = {"WB": "WB", "OZON": "Ozon", "YANDEX MARKET": "Яндекс"}
    cards = []
    for marketplace in db.MARKETPLACES:
        item = market_data.get(marketplace, {})
        coverage = item.get("coverage_days")
        tone, status_label = _stock2_aggregate_status(item)
        cards.append(
            f'<article class="stock2-market stock2-market--{tone}">'
            '<div class="stock2-market-top">'
            f"<strong>{html.escape(labels.get(marketplace, marketplace))}</strong>"
            f"{_stock2_badge(tone, status_label)}"
            "</div>"
            f"<div><span>Остаток</span><b>{_fmt_num(int(item.get('total_stock') or 0))}</b></div>"
            f"<div><span>SKU с остатком</span><b>{_fmt_num(int(item.get('stock_sku_count') or 0))}</b></div>"
            f"<div><span>Продаж/день</span><b>{_fmt_float(float(item.get('avg_daily') or 0))}</b></div>"
            f"<div><span>Покрытие</span><b>{_fmt_float(coverage)} дн.</b></div>"
            "</article>"
        )
    return "\n".join(cards)


def _stock2_render_bars(store_data: dict[str, dict], store_slugs: list[str] | None = None) -> str:
    rows = []
    for slug in store_slugs if store_slugs is not None else list(STORES):
        store = STORES[slug]
        item = store_data.get(slug, {})
        coverage = item.get("coverage_days")
        width = 0 if coverage is None else min(100, max(4, coverage / 60 * 100))
        tone, _ = _stock2_aggregate_status(item)
        if coverage is not None:
            label = f"{_fmt_float(coverage)} дн."
        elif int(item.get("no_data_count") or 0):
            label = "данные не загружены"
        else:
            label = "нет продаж 30 дн."
        rows.append(
            '<div class="stock2-bar">'
            f"<span>{html.escape(store['name'])}</span>"
            f'<div class="stock2-track"><i class="stock2-fill stock2-fill--{tone}" style="width:{width:.1f}%"></i></div>'
            f"<strong>{html.escape(label)}</strong>"
            "</div>"
        )
    return "\n".join(rows)


def _stock2_render_stores(store_data: dict[str, dict], store_slugs: list[str] | None = None) -> str:
    labels = {"WB": "WB", "OZON": "Ozon", "YANDEX MARKET": "Яндекс"}
    cards = []
    for slug in store_slugs if store_slugs is not None else list(STORES):
        store = STORES[slug]
        item = store_data.get(slug, {})
        coverage = item.get("coverage_days")
        tone, status_label = _stock2_aggregate_status(item)
        markets = (
            "".join(
                f"<span>{html.escape(labels.get(mp, mp))}</span>"
                for mp in db.MARKETPLACES
                if mp in item.get("marketplaces", set())
            )
            or "<span>нет данных</span>"
        )
        cards.append(
            f'<article class="stock2-store" style="--store-color:{store["color"]};--store-text:{store["text"]}">'
            '<div class="stock2-store-top">'
            f'<span class="stock2-avatar">{html.escape(store["initials"])}</span>'
            f"{_stock2_badge(tone, status_label)}"
            "</div>"
            f"<h3>{html.escape(store['name'])}</h3>"
            f'<strong class="stock2-store-stock">{_fmt_num(int(item.get("total_stock") or 0))}</strong>'
            '<div class="stock2-store-meta">'
            f"<span>{_fmt_num(int(item.get('sku_count') or 0))} SKU</span>"
            f"<b>{_fmt_float(coverage)} дн.</b>"
            "</div>"
            f'<div class="stock2-store-chips">{markets}</div>'
            "</article>"
        )
    return "\n".join(cards)


def _stock2_table_rows(rows: list[dict], frozen: bool = False, limit: int | None = 40) -> str:
    if not rows:
        colspan = 7
        return f'<tr><td colspan="{colspan}" class="stock2-empty">Нет данных для таблицы</td></tr>'
    body = []
    visible_rows = rows if limit is None else rows[:limit]
    for row in visible_rows:
        if frozen:
            last_sold = format_dt(row["last_sold_at"]) if row.get("last_sold_at") else "продаж не было"
            body.append(
                "<tr>"
                f"<td>{_stock2_product_cell(row)}</td>"
                f"<td>{html.escape(row['store_name'])}</td>"
                f"<td>{html.escape(row['marketplace'])}</td>"
                f"<td>{_fmt_num(int(row['total_stock']))}</td>"
                f"<td>{_fmt_money(row.get('purchase_price'))}</td>"
                f"<td>{_fmt_money(row.get('stock_value'))}</td>"
                f"<td>{html.escape(last_sold)}</td>"
                "</tr>"
            )
        else:
            tone, label = _stock2_status(row)
            coverage = row.get("coverage_days")
            body.append(
                "<tr>"
                f"<td>{_stock2_product_cell(row)}</td>"
                f"<td>{html.escape(row['store_name'])}</td>"
                f"<td>{html.escape(row['marketplace'])}</td>"
                f"<td>{_fmt_num(int(row['total_stock']))}</td>"
                f"<td>{_fmt_float(float(row['avg_daily']))}</td>"
                f"<td>{_fmt_float(coverage)} дн.</td>"
                f"<td>{_stock2_badge(tone, label)}</td>"
                "</tr>"
            )
    return "".join(body)


def _stock2_split_rows(rows: list[dict]) -> dict[str, list[dict]]:
    return {
        "ending-7": sorted(
            [
                row
                for row in rows
                if row["total_stock"] > 0
                and row.get("coverage_days") is not None
                and row["coverage_days"] <= 7
            ],
            key=lambda row: (row["coverage_days"], -row["avg_daily"]),
        ),
        "zero": sorted(
            [row for row in rows if row["total_stock"] <= 0],
            key=lambda row: (-int(row.get("sold_30") or 0), row["store_name"], row["name"] or ""),
        ),
        "excess": sorted(
            [
                row
                for row in rows
                if row["total_stock"] > 0
                and row.get("coverage_days") is not None
                and row["coverage_days"] >= settings.stock_excess_days
            ],
            key=lambda row: (-row["coverage_days"], -row["total_stock"]),
        ),
        "frozen": sorted(
            [
                row
                for row in rows
                if row["total_stock"] > 0 and row.get("sales_loaded") and int(row.get("sold_60") or 0) <= 0
            ],
            key=lambda row: (-(row.get("stock_value") or 0), -row["total_stock"]),
        ),
    }


def _stock2_pagination(kind: str, page: int, total_rows: int) -> str:
    total_pages = max(1, math.ceil(total_rows / settings.stock_detail_page_size))
    if total_pages <= 1:
        return ""

    previous = (
        f'<a class="stock2-page-button" href="/stock-2/details/{kind}?page={page - 1}">← Предыдущая</a>'
        if page > 1
        else '<span class="stock2-page-button is-disabled">← Предыдущая</span>'
    )
    following = (
        f'<a class="stock2-page-button" href="/stock-2/details/{kind}?page={page + 1}">Следующая →</a>'
        if page < total_pages
        else '<span class="stock2-page-button is-disabled">Следующая →</span>'
    )
    first_row = (page - 1) * settings.stock_detail_page_size + 1
    last_row = min(total_rows, page * settings.stock_detail_page_size)
    return (
        '<nav class="stock2-pagination" aria-label="Страницы детализации">'
        f"{previous}"
        f"<span>Страница <strong>{_fmt_num(page)}</strong> из {_fmt_num(total_pages)}"
        f"<small>Показаны {_fmt_num(first_row)}–{_fmt_num(last_row)} из {_fmt_num(total_rows)}</small></span>"
        f"{following}"
        "</nav>"
    )


STOCK2_DETAIL_META = {
    "ending-7": {
        "title": "Закончатся до 7 дней",
        "table": "SKU требуют пополнения",
        "note": "Сортировка: сначала минимальное покрытие, затем максимальная скорость продаж.",
        "frozen": False,
    },
    "zero": {
        "title": "Нулевой остаток",
        "table": "SKU без остатка",
        "note": "Сверху товары, которые продавались за последние 30 дней.",
        "frozen": False,
    },
    "excess": {
        "title": "Избыточный запас",
        "table": "SKU с высоким покрытием",
        "note": "Сортировка: сначала максимальное покрытие и максимальный остаток.",
        "frozen": False,
    },
    "frozen": {
        "title": "Замороженный запас",
        "table": "SKU без движения",
        "note": "Сортировка: сначала максимальная стоимость остатка, если известна себестоимость.",
        "frozen": True,
    },
}


@router.get("/stock-2", response_class=HTMLResponse)
async def stock2(request: Request):
    allowed_stores = accessible_store_slugs(request.state.user)
    allowed_set = set(allowed_stores)
    rows = await run_in_threadpool(_stock2_inventory_rows)
    rows = [row for row in rows if row["store_slug"] in allowed_set]
    problems = await run_in_threadpool(health.stores_with_problems)
    problems = {slug: items for slug, items in problems.items() if slug in allowed_set}

    store_data = _stock2_aggregate(rows, "store_slug")
    market_data = _stock2_aggregate(rows, "marketplace")

    total_stock = sum(int(row["total_stock"]) for row in rows)
    total_sku = sum(1 for row in rows if row.get("in_catalog"))
    total_connections = sum(len(item.get("marketplaces", set())) for item in store_data.values())
    coverage_rows = [row for row in rows if row.get("sales_loaded")]
    coverage_stock = sum(int(row["total_stock"]) for row in coverage_rows)
    total_avg_daily = sum(float(row["avg_daily"] or 0) for row in coverage_rows)
    total_coverage = coverage_stock / total_avg_daily if total_avg_daily > 0 else None
    loaded_connections = len({(row["store_slug"], row["marketplace"]) for row in coverage_rows})
    total_value = sum(float(row["stock_value"] or 0) for row in rows if row.get("stock_value") is not None)
    known_value_rows = sum(1 for row in rows if row.get("stock_value") is not None)

    detail_rows = _stock2_split_rows(rows)
    ending_7 = detail_rows["ending-7"]
    zero_stock = detail_rows["zero"]
    excess_stock = detail_rows["excess"]
    frozen_stock = detail_rows["frozen"]

    top_risk_store = "нет"
    if ending_7:
        by_store = _stock2_aggregate(ending_7, "store_slug")
        top_slug, top_item = max(by_store.items(), key=lambda pair: pair[1]["risk_count"])
        top_risk_store = (
            f"{STORES.get(top_slug, {}).get('name', top_slug.upper())}: {top_item['risk_count']} SKU"
        )

    summary_cards = "\n".join(
        [
            _stock2_summary_card(
                "Магазинов",
                _fmt_num(len(allowed_stores)),
                f"{_fmt_num(total_connections)} подключений",
                "neutral",
            ),
            _stock2_summary_card(
                "Общий остаток", _fmt_num(total_stock), f"{_fmt_num(total_sku)} SKU в каталогах", "blue"
            ),
            _stock2_summary_card(
                "Покрытие",
                f"{_fmt_float(total_coverage)} дн.",
                f"данные по {_fmt_num(loaded_connections)} из {_fmt_num(total_connections)} подключений",
                "green" if loaded_connections == total_connections else "warning",
            ),
            _stock2_summary_card(
                "Себестоимость остатков",
                _fmt_money(total_value),
                f"известна по {_fmt_num(known_value_rows)} SKU",
                "violet",
            ),
            _stock2_summary_card(
                "Состояние данных",
                "В норме" if not problems else _fmt_num(sum(len(v) for v in problems.values())),
                "ошибки доступа к кабинетам" if problems else "ошибок доступа нет",
                "warning" if problems else "green",
            ),
        ]
    )

    attention_cards = "\n".join(
        [
            _stock2_attention_card(
                "Закончатся до 7 дней",
                f"{_fmt_num(len(ending_7))} SKU",
                f"Основной риск: {top_risk_store}",
                "danger",
                "/stock-2/details/ending-7",
                "Открыть полную детализацию",
            ),
            _stock2_attention_card(
                "Нулевой остаток",
                f"{_fmt_num(len(zero_stock))} SKU",
                f"{_fmt_num(sum(1 for row in zero_stock if int(row.get('sold_30') or 0) > 0))} SKU продавались за 30 дней",
                "warning",
                "/stock-2/details/zero",
                "Открыть полную детализацию",
            ),
            _stock2_attention_card(
                "Избыточный запас",
                f"{_fmt_num(len(excess_stock))} SKU",
                "Покрытие выше 90 дней по истории продаж",
                "blue",
                "/stock-2/details/excess",
                "Открыть полную детализацию",
            ),
            _stock2_attention_card(
                "Замороженный запас",
                f"{_fmt_num(len(frozen_stock))} SKU",
                f"{_fmt_money(sum((row.get('stock_value') or 0) for row in frozen_stock))} без продаж 60 дней",
                "violet",
                "/stock-2/details/frozen",
                "Открыть полную детализацию",
            ),
        ]
    )

    last_sync = await run_in_threadpool(db.get_last_sync_at)
    content = fill_template(
        "stock2_content.html",
        last_sync=html.escape(format_dt(last_sync)),
        summary_cards=summary_cards,
        attention_cards=attention_cards,
        marketplace_cards=_stock2_render_marketplaces(market_data),
        coverage_bars=_stock2_render_bars(store_data, allowed_stores),
        store_cards=_stock2_render_stores(store_data, allowed_stores),
    )
    return render_page(
        "CheckStock — Остатки 2",
        "stock2",
        content,
        request.state.user,
        content_class="content--stock2",
    )


@router.get("/stock-2/details/{kind}", response_class=HTMLResponse)
async def stock2_details(request: Request, kind: str, page: int = 1):
    meta = STOCK2_DETAIL_META.get(kind)
    if meta is None:
        raise HTTPException(status_code=404, detail="Детализация не найдена")

    rows = await run_in_threadpool(_stock2_inventory_rows)
    allowed_set = set(accessible_store_slugs(request.state.user))
    rows = [row for row in rows if row["store_slug"] in allowed_set]
    detail_rows = _stock2_split_rows(rows)[kind]
    total_pages = max(1, math.ceil(len(detail_rows) / settings.stock_detail_page_size))
    page = min(max(page, 1), total_pages)
    start = (page - 1) * settings.stock_detail_page_size
    visible_rows = detail_rows[start : start + settings.stock_detail_page_size]
    frozen = bool(meta["frozen"])
    table_head = (
        "<tr><th>Товар</th><th>Кабинет</th><th>Площадка</th>"
        "<th>Остаток</th><th>Себестоимость</th>"
        "<th>Стоимость остатка</th><th>Последняя продажа</th></tr>"
        if frozen
        else "<tr><th>Товар</th><th>Кабинет</th><th>Площадка</th>"
        "<th>Остаток</th><th>Продаж/день</th><th>Хватит</th><th>Статус</th></tr>"
    )

    content = fill_template(
        "stock2_detail_content.html",
        detail_count=_fmt_num(len(detail_rows)),
        table_title=html.escape(str(meta["table"])),
        table_note=html.escape(str(meta["note"])),
        table_head=table_head,
        table_rows=_stock2_table_rows(visible_rows, frozen=frozen, limit=None),
        pagination=_stock2_pagination(kind, page, len(detail_rows)),
    )
    return render_page(
        f"CheckStock — Остатки 2 — {meta['title']}",
        "stock2",
        content,
        request.state.user,
        content_class="content--stock2",
    )
