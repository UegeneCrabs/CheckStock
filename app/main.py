import asyncio
import html
import json
import logging
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from string import Template

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app import auth, db, health, stores
from app.ff_import import importer as ff_stock_import
from app.ff_import import shipment as ff_shipment
from app.ff_import import transfer as ff_transfer
from app.ff_import import export as ff_export
from app.formatting import format_dt
from app.ozon import catalog as ozon_catalog
from app.ozon import sync as ozon_sync
from app.yandex import catalog as ya_catalog
from app.yandex import sync as ya_sync
from app.wb import catalog as wb_catalog
from app.wb import sync as wb_sync
from app.wb import token_watch

STORES = stores.STORES

# Маркетплейсы, по которым синхронизация уже реализована. Остальные
# показываются с пометкой «скоро» и заглушкой вместо таблицы.
READY_MARKETPLACES = {"WB", "OZON", "YANDEX MARKET"}

# Какие схемы продаж есть у каждого маркетплейса. У WB схемы две, у Ozon
# добавляется rFBS (склад продавца с доставкой Ozon), поэтому набор колонок
# в таблице остатков зависит от выбранного маркетплейса.
MARKETPLACE_SCHEMES = {
    "WB": [("fbs", "Текущий сток в продаже FBS"), ("fbo", "Текущий сток в продаже FBO")],
    "OZON": [
        ("fbs", "Текущий сток в продаже FBS"),
        ("rfbs", "Текущий сток в продаже rFBS"),
        ("fbo", "Текущий сток в продаже FBO"),
    ],
    # Для Яндекса это запасной вариант: реальный набор колонок зависит от
    # магазина и собирается в ya_sync.store_schemes по его кабинетам.
    "YANDEX MARKET": [("fbo", "FBY — склады Маркета")],
}


def schemes_for(marketplace: str, store_slug: str = "") -> list[tuple[str, str]]:
    """Колонки остатков для площадки.

    У WB и Ozon набор одинаков для всех магазинов. У Яндекса — нет: там на
    каждый FBS-магазин своя колонка, потому что это разные склады разных
    партнёров, и у одного продавца их может быть три, а у другого ни одного.
    """
    if marketplace == "YANDEX MARKET" and store_slug:
        schemes = ya_sync.store_schemes(store_slug)
        if schemes:
            return schemes
    return MARKETPLACE_SCHEMES.get(marketplace, MARKETPLACE_SCHEMES["WB"])


def render_stock_head(marketplace: str, store_slug: str = "") -> str:
    """Шапка таблицы остатков — набор колонок зависит от маркетплейса."""
    cells = [
        "<th>Артикул</th>", "<th>Баркод</th>", "<th>Название</th>", "<th>Тотал</th>",
        "<th>Доступно ФФ для распределения</th>", "<th>Сток ФФ по API</th>",
    ]
    cells += [
        f'<th class="col-scheme col-{scheme}">{html.escape(title)}</th>'
        for scheme, title in schemes_for(marketplace, store_slug)
    ]
    cells.append('<th class="col-filler"></th>')
    return "<tr>" + "".join(cells) + "</tr>"

# Логи приложения — в stdout, откуда их забирает journald на сервере.
#
# Без этого сообщения синхронизации пропадали: uvicorn настраивает логирование
# только для своих логгеров, а наши остаются без обработчика, и всё ниже
# WARNING молча теряется. В журнале были видны запросы, но не было ни строчки
# о том, выгрузились каталоги или нет, — разбирать сбои приходилось запросами
# в базу.
#
# force=True обязателен: uvicorn к этому моменту уже трогал корневой логгер,
# и обычный basicConfig не сделал бы ничего.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
    force=True,
)

logger = logging.getLogger("checkstock.sync")

# Как часто сама, в фоне, тянуть остатки (в секундах). 30 минут — тот же
# темп, с которым сами площадки обновляют данные по остаткам.
AUTO_SYNC_INTERVAL_SECONDS = 30 * 60

# Во сколько ночью обновлять каталоги (час по времени сервера).
#
# Каталог живёт отдельно от остатков, потому что меняется несравнимо реже:
# остаток скачет весь день, а карточку заводят раз в неделю. Тянуть весь
# ассортимент трёх площадок каждые полчаса — это лимиты, потраченные на
# данные, которые почти всегда те же самые.
#
# Ночь выбрана по той же причине, по которой её выбирают для любой тяжёлой
# выгрузки: никто не работает, лимиты свободны, и если что-то пойдёт не так,
# это не совпадёт с отгрузкой.
CATALOG_SYNC_HOUR = 3

# Каталоги выгружены хотя бы раз — остатки ждут этого события.
# Остаток пишется только по товарам из каталога, поэтому на пустой базе
# синхронизация остатков, начатая первой, не записала бы ничего.
_catalog_ready = asyncio.Event()


def _sync_catalogs() -> dict:
    """Каталоги всех трёх площадок. Возвращает отчёт для лога.

    Последовательно: это разные кабинеты, но выгрузка каталога тяжёлая, а
    спешить некуда — она идёт раз в сутки.
    """
    report = {}
    for marketplace, sync in (("WB", wb_catalog.sync_all),
                              ("OZON", ozon_catalog.sync_all),
                              ("YANDEX MARKET", ya_catalog.sync_all)):
        try:
            report[marketplace] = sync()
        except Exception as e:
            # Каждая площадка отдельно: упавший WB не должен отменять
            # выгрузку каталогов Ozon и Яндекса — они друг с другом никак
            # не связаны, и терять два каталога из-за одного незачем.
            logger.error("Каталог %s не выгружен — %s: %s", marketplace, type(e).__name__, e)
            logger.debug("Подробности ошибки каталога %s", marketplace, exc_info=True)
            report[marketplace] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return report


def _seconds_until_next_run(hour: int) -> float:
    """Сколько секунд до ближайшего наступления указанного часа."""
    now = datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _catalog_sync_loop() -> None:
    """Каталоги: один раз при запуске и дальше каждую ночь.

    При запуске — обязательно: базу могли почистить, подключить новый магазин
    или добавить ключ, и ждать до ночи в таком состоянии система не должна.
    """
    first = True
    while True:
        try:
            report = await run_in_threadpool(_sync_catalogs)
            logger.info("Каталоги обновлены (%s): %s",
                        "старт" if first else "ночная выгрузка", report)
        except Exception:
            logger.exception("Выгрузка каталогов упала с ошибкой")
        finally:
            # Событие ставим в любом случае: если каталоги не выгрузились,
            # остатки всё равно должны пойти по тому, что уже есть в базе,
            # иначе одна сломанная площадка остановила бы всю синхронизацию.
            _catalog_ready.set()
            first = False

        wait = _seconds_until_next_run(CATALOG_SYNC_HOUR)
        logger.info("Следующая выгрузка каталогов через %.1f ч", wait / 3600)
        await asyncio.sleep(wait)


async def _auto_sync_loop() -> None:
    """Остатки — каждые полчаса. Каталоги здесь не трогаем, см. _catalog_sync_loop."""
    # Первый заход ждёт каталоги: на чистой базе писать остатки не по чему.
    await _catalog_ready.wait()

    while True:
        try:
            report = await run_in_threadpool(wb_sync.sync_all)
            logger.info("Автосинхронизация WB завершена: %s", report)
            ozon_report = await run_in_threadpool(ozon_sync.sync_all)
            logger.info("Автосинхронизация Ozon завершена: %s", ozon_report)
            ya_report = await run_in_threadpool(ya_sync.sync_all)
            logger.info("Автосинхронизация Яндекса завершена: %s", ya_report)
        except Exception:
            logger.exception("Автосинхронизация остатков упала с ошибкой")
        await asyncio.sleep(AUTO_SYNC_INTERVAL_SECONDS)


async def _token_watch_loop() -> None:
    """Раз в 6 часов смотрим, не пора ли перечитать сроки действия ключей.
    Сама проверка локальная (срок зашит в токене), поэтому это дёшево."""
    while True:
        try:
            last = await run_in_threadpool(db.get_last_token_check)
            if token_watch.should_refresh(last):
                await run_in_threadpool(token_watch.refresh_token_info)
        except Exception:
            logger.exception("Проверка сроков действия ключей WB упала")
        await asyncio.sleep(6 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    db.seed_defaults()
    auth.seed_superadmin()
    catalog_task = asyncio.create_task(_catalog_sync_loop())
    sync_task = asyncio.create_task(_auto_sync_loop())
    token_task = asyncio.create_task(_token_watch_loop())
    try:
        yield
    finally:
        catalog_task.cancel()
        sync_task.cancel()
        token_task.cancel()


app = FastAPI(lifespan=lifespan)

# app/main.py живёт на уровень глубже корня проекта — templates/ и static/
# остаются в корне (рядом с secrets/, data/), поэтому поднимаемся на уровень выше.
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# Пути, доступные без входа: сама форма логина и статика (иначе страница входа
# приедет без стилей). Всё остальное закрыто — это внутренний инструмент.
PUBLIC_PATHS = {"/login", "/logout"}


@app.middleware("http")
async def require_auth(request: Request, call_next):
    """Пускает дальше только авторизованных. Для обычных страниц — редирект на
    /login, для запросов из JS (fetch) — честный 401, чтобы фронт не пытался
    показать HTML формы логина вместо JSON."""
    path = request.url.path

    if path in PUBLIC_PATHS or path.startswith("/static/"):
        request.state.user = None
        return await call_next(request)

    user = await run_in_threadpool(auth.user_for_token, request.cookies.get(auth.SESSION_COOKIE, ""))
    request.state.user = user

    if user is None:
        wants_json = "application/json" in request.headers.get("accept", "") or (
            request.headers.get("x-requested-with") == "fetch"
        )
        if wants_json or request.method != "GET":
            return JSONResponse({"ok": False, "error": "Требуется вход в систему"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    return await call_next(request)


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    # уже вошли — незачем показывать форму
    if await run_in_threadpool(auth.user_for_token, request.cookies.get(auth.SESSION_COOKIE, "")):
        return RedirectResponse("/stock", status_code=303)
    return fill_template("login.html", error="")


@app.post("/login", response_class=HTMLResponse)
async def login_submit(login: str = Form(""), password: str = Form("")):
    user = await run_in_threadpool(auth.authenticate, login, password)
    if user is None:
        page = fill_template(
            "login.html",
            error='<p class="login-error">Неверный логин или пароль</p>',
        )
        return HTMLResponse(page, status_code=401)

    token = await run_in_threadpool(auth.start_session, user["id"])
    response = RedirectResponse("/stock", status_code=303)
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        httponly=True,       # кука недоступна из JS — снижает урон от XSS
        samesite="lax",      # не уезжает на сторонние сайты
        max_age=auth.SESSION_TTL_DAYS * 24 * 3600,
        path="/",
    )
    return response


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get(auth.SESSION_COOKIE, "")
    if token:
        await run_in_threadpool(auth.end_session, token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response


def read_template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def fill_template(name: str, **values: str) -> str:
    return Template(read_template(name)).substitute(**values)


def render_token_banner() -> str:
    """Предупреждение о протухающем ключе WB — показывается на всех страницах,
    пока ключ не заменят."""
    warnings = token_watch.get_warnings()
    if not warnings:
        return ""

    items = []
    for w in warnings:
        when = format_dt(w["expires_at"])
        if w["expired"]:
            items.append(f"<strong>{html.escape(w['store'])}</strong> — ключ уже недействителен (истёк {when})")
        else:
            days = w["days_left"]
            tail = "сегодня" if days <= 0 else f"через {days} дн."
            items.append(f"<strong>{html.escape(w['store'])}</strong> — ключ истекает {tail} ({when})")

    return (
        '<div class="token-banner">'
        '<span class="token-banner-icon">!</span>'
        "<div><p class=\"token-banner-title\">Скоро закончится срок действия ключа WB</p>"
        f'<p class="token-banner-text">{"; ".join(items)}. '
        "Сообщите администратору о необходимости замены ключа — иначе остатки перестанут обновляться.</p></div>"
        "</div>"
    )


def render_page(title: str, active: str, content: str, user: dict | None = None) -> str:
    admin_link = ""
    if auth.has_role(user, "admin"):
        cls = "active" if active == "admin" else ""
        admin_link = f'                    <li><a href="/admin" class="{cls}">Админка</a></li>'

    header = fill_template(
        "header.html",
        supply_active="active" if active == "supply" else "",
        stock_active="active" if active == "stock" else "",
        admin_link=admin_link,
        user_name=html.escape(user["full_name"]) if user else "",
        user_role=html.escape(db.ROLE_LABELS.get(user["role"], user["role"])) if user else "",
    )
    return fill_template(
        "page.html", title=title, header=header,
        content=render_token_banner() + content,
    )


def render_ff_options() -> str:
    return "\n".join(
        f'                    <option value="{html.escape(name)}">{html.escape(name)}</option>'
        for name in db.get_fulfillments()
    )


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _fmt_num(value: int) -> str:
    """Разряды через пробел: 1234567 -> '1 234 567'. Тот же формат повторён
    в JS на странице (функция fmt), чтобы пересчитанные значения выглядели
    так же, как отрисованные сервером."""
    return f"{value:,}".replace(",", " ")  # неразрывный пробел: число не рвётся при переносе


def _cell(value: int | None) -> str:
    """0 и пустое показываем прочерком — так таблица читается спокойнее."""
    return _fmt_num(value) if value else "—"


# Остаток на фулфилменте — это товар, ещё НЕ отправленный в продажу.
# В подписи это сказано прямо: без пометки «WB» в форме перемещения читается
# как «остаток на витрине WB», хотя речь про нераспределённый товар на складе.
UNALLOCATED_SUFFIX = "не распределено"


def marketplace_move_label(name: str) -> str:
    return f"{name} — {UNALLOCATED_SUFFIX}"


def render_mp_move_options() -> str:
    """Маркетплейсы для форм перемещения и отгрузки. Здесь выбор обязателен:
    операция всегда идёт из конкретной ячейки, «все сразу» смысла не имеет."""
    return "\n".join(
        f'                                <option value="{html.escape(name)}"'
        f'{" selected" if name == db.DEFAULT_MARKETPLACE else ""}>'
        f'{html.escape(marketplace_move_label(name))}</option>'
        for name in db.MARKETPLACES
    )


def marketplace_ready(marketplace: str, store_slug: str) -> bool:
    """Есть ли по этой площадке данные у этого магазина.

    Готовность считается по магазину, а не по системе целиком: поддержка
    площадки может быть написана, но у конкретного магазина не быть ключа —
    и тогда показывать пустую таблицу нечестно, она выглядит как «остатков
    нет», хотя на деле их просто неоткуда взять.
    """
    if marketplace not in READY_MARKETPLACES:
        return False
    return health.has_token(marketplace, store_slug)


def render_mp_tabs(active: str, store_slug: str = "") -> str:
    """Вкладки маркетплейсов над блоками.

    Вкладки показываем все и всегда — по ним видно, где магазин вообще
    представлен. Те, по которым данных нет, помечаем и открываем заглушкой
    вместо таблицы.
    """
    tabs = []
    for name in db.MARKETPLACES:
        ready = marketplace_ready(name, store_slug)
        classes = "mp-tab" + (" active" if name == active else "")
        classes += "" if ready else " mp-tab--nokey"
        tabs.append(
            f'            <button class="{classes}" type="button" role="tab" '
            f'data-mp="{html.escape(name)}" data-ready="{"1" if ready else "0"}">'
            f"{html.escape(name)}</button>"
        )
    return "\n".join(tabs)


def render_stock_rows(store_slug: str, marketplace: str) -> str:
    schemes = schemes_for(marketplace, store_slug)
    items = db.get_stock_items(store_slug, marketplace, tuple(k for k, _ in schemes))
    if not items:
        return '                            <tr class="empty-row"><td colspan="10">Пока нет остатков по этому магазину</td></tr>'

    rows = []
    for item in items:
        ff_available = item["ff_available"] or 0
        # Ключи схем приходят из запроса полями "<схема>_stock" — набор
        # зависит от площадки и магазина, поэтому собираем по списку.
        by_scheme = {scheme: (item[f"{scheme}_stock"] or 0) for scheme, _ in schemes}
        sale_total = sum(by_scheme.values())
        row_total = ff_available + sale_total
        # Красным подсвечиваем товар, который лежит на ФФ, но нигде не
        # продаётся: есть остаток к распределению, а на витрине по всем
        # схемам ноль. Перечислять схемы поимённо нельзя — у Яндекса их
        # столько, сколько у продавца FBS-партнёров.
        stuck = ff_available > 0 and sale_total == 0
        row_class = ' class="row-alert"' if stuck else ""
        rows.append(
            f'                            <tr{row_class} data-article="{html.escape(item["article"])}">'
            f"<td>{html.escape(item['article'])}</td>"
            f"<td>{html.escape(item['barcode'])}</td>"
            f"<td>{html.escape(item['name'])}</td>"
            f'<td class="col-row-total">{_cell(row_total)}</td>'
            f'<td class="col-ff-available">{_cell(ff_available)}</td>'
            '<td class="col-ff-info"></td>'
            + "".join(
                f'<td class="col-scheme col-{scheme}">{_cell(by_scheme[scheme])}</td>'
                for scheme, _ in schemes
            )
            + '<td class="col-filler"></td>'
            "</tr>"
        )
    return "\n".join(rows)


def render_stock_totals(store_slug: str, marketplace: str) -> str:
    """Строка итогов по столбцам — идёт сразу после шапки и закреплена вместе с ней."""
    schemes = schemes_for(marketplace, store_slug)
    items = db.get_stock_items(store_slug, marketplace, tuple(k for k, _ in schemes))

    ff_total = sum(item["ff_available"] or 0 for item in items)
    scheme_totals = {
        scheme: sum(item[f"{scheme}_stock"] or 0 for item in items)
        for scheme, _ in schemes
    }
    grand_total = ff_total + sum(scheme_totals.values())

    return (
        '<tr class="totals-row">'
        '<th colspan="3" class="totals-label">Итого</th>'
        f'<th class="tot-grand">{_fmt_num(grand_total)}</th>'
        f'<th class="tot-ff">{_fmt_num(ff_total)}</th>'
        '<th class="col-ff-info"></th>'
        + "".join(
            f'<th class="col-scheme tot-{scheme}">{_fmt_num(scheme_totals[scheme])}</th>'
            for scheme, _ in schemes
        )
        + '<th class="col-filler"></th>'
        "</tr>"
    )


# Сколько складов показываем отдельными колонками, прежде чем схлопнуть
# остальные. У Ozon складов 45 — такая таблица нечитаема, а хвост из них
# держит единицы штук. Восемь выбраны как компромисс: помещается по ширине
# и покрывает подавляющую долю остатка.
TOP_WAREHOUSES = 8
OTHER_WAREHOUSES_LABEL = "Другие склады"


def _pick_top_warehouses(rows_data: list[dict], top_n: int) -> tuple[list[str], set[str]]:
    """Топ складов по суммарному остатку и множество «остальных».

    Считаем каждый раз заново, а не запоминаем список: склады у Ozon
    меняются местами от поставки к поставке, и зафиксированный однажды топ
    быстро перестал бы отражать реальность.
    """
    totals: dict[str, int] = {}
    for row in rows_data:
        totals[row["warehouse"]] = totals.get(row["warehouse"], 0) + (row["quantity"] or 0)

    if len(totals) <= top_n:
        return sorted(totals), set()

    # по убыванию остатка, при равенстве — по алфавиту, чтобы порядок колонок
    # не прыгал между обновлениями страницы
    ranked = sorted(totals, key=lambda w: (-totals[w], w))
    top = ranked[:top_n]
    return top, set(ranked[top_n:])


def render_trash_table(store_slug: str, marketplace: str, can_edit: bool) -> str:
    """Мусорка отдельной таблицей, а не общей: у неё есть отметка
    «разобрались», которой нет у обычных складов."""
    rows_data = db.get_trash_details(store_slug, marketplace)
    if not rows_data:
        return (
            '<table class="data-table data-table--trash">'
            "<thead><tr><th>Нет данных</th></tr></thead>"
            '<tbody><tr class="empty-row">'
            "<td>Мусорка пуста — потерянного товара по этому маркетплейсу нет</td>"
            "</tr></tbody></table>"
        )

    total = sum(int(r["quantity"] or 0) for r in rows_data)
    body = []
    for row in rows_data:
        checked = bool(row.get("checked"))
        quantity = int(row["quantity"] or 0)
        # отрицательное количество — излишек: ФФ отдал больше, чем числилось
        qty_class = "trash-qty trash-qty--surplus" if quantity < 0 else "trash-qty"
        body.append(
            f'<tr class="{"is-checked" if checked else ""}">'
            f'<td data-label="Баркод">{html.escape(row["barcode"] or "")}</td>'
            f'<td data-label="Артикул">{html.escape(row["article"])}</td>'
            f'<td data-label="Название">{html.escape(row["name"] or "")}</td>'
            f'<td data-label="Склад">{html.escape(row["warehouse"] or "")}</td>'
            f'<td data-label="Количество" class="{qty_class}">{_fmt_num(quantity)}</td>'
            f'<td data-label="Проконтролировано">'
            f'<label class="trash-check">'
            f'<input type="checkbox" class="trash-checkbox"'
            f' data-article="{html.escape(row["article"], quote=True)}"'
            f' data-warehouse="{html.escape(row["warehouse"] or "", quote=True)}"'
            f'{" checked" if checked else ""}{"" if can_edit else " disabled"}>'
            f"</label></td>"
            "</tr>"
        )

    return (
        '<table class="data-table data-table--trash" data-table-filter>'
        "<thead><tr>"
        "<th>Баркод</th><th>Артикул</th><th>Название</th><th>Склад</th>"
        "<th>Количество</th><th>Проконтролировано</th>"
        "</tr>"
        '<tr class="totals-row">'
        '<th colspan="4" class="totals-label">Итого</th>'
        f'<th class="tot-grand">{_fmt_num(total)}</th><th></th>'
        "</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def render_warehouse_table(rows_data: list[dict], empty_text: str,
                           top_n: int | None = None) -> str:
    """Сводная таблица: баркод/артикул/название в строках, склады — отдельными
    колонками, на пересечении — остаток на этом складе по этому товару.
    Форма данных одинаковая для FBO / FBS / ФФ, поэтому таблица одна на всех.

    top_n — показать столько складов, а остаток по прочим свернуть в одну
    колонку «Другие склады». Нужно там, где складов десятки."""
    if not rows_data:
        return (
            '<table class="data-table data-table--warehouses">'
            "<thead><tr><th>Нет данных</th></tr></thead>"
            '<tbody><tr class="empty-row">'
            f"<td>{html.escape(empty_text)}</td>"
            "</tr></tbody></table>"
        )

    other: set[str] = set()
    if top_n:
        warehouse_list, other = _pick_top_warehouses(rows_data, top_n)
    else:
        warehouse_list = sorted({row["warehouse"] for row in rows_data})

    # Товары в порядке появления. Остаток по свёрнутым складам суммируется
    # в отдельную колонку, чтобы тотал строки остался верным.
    products: dict[tuple[str, str, str], dict[str, int]] = {}

    for row in rows_data:
        key = (row["barcode"], row["article"], row["name"])
        column = OTHER_WAREHOUSES_LABEL if row["warehouse"] in other else row["warehouse"]
        cells = products.setdefault(key, {})
        cells[column] = cells.get(column, 0) + (row["quantity"] or 0)

    if other:
        warehouse_list = warehouse_list + [OTHER_WAREHOUSES_LABEL]

    # Итоги по колонкам. Считаем по тем же данным, что и ячейки, а не
    # отдельным запросом: иначе при любом расхождении в фильтрации итог
    # разошёлся бы с суммой видимых чисел, и доверия к таблице не осталось.
    column_totals: dict[str, int] = {}
    for cells in products.values():
        for column, quantity in cells.items():
            column_totals[column] = column_totals.get(column, 0) + quantity
    grand_total = sum(column_totals.values())

    header_cells = "".join(
        f'<th class="col-other" title="Сумма по {len(other)} складам вне топ-{top_n}">'
        f"{html.escape(w)}</th>"
        if w == OTHER_WAREHOUSES_LABEL and other
        else f"<th>{html.escape(w)}</th>"
        for w in warehouse_list
    )
    totals_cells = "".join(
        f'<th class="tot-warehouse{" col-other" if w == OTHER_WAREHOUSES_LABEL and other else ""}">'
        f"{_fmt_num(column_totals.get(w, 0))}</th>"
        for w in warehouse_list
    )
    totals_row = (
        '<tr class="totals-row">'
        '<th colspan="3" class="totals-label">Итого</th>'
        f'<th class="tot-grand">{_fmt_num(grand_total)}</th>'
        f"{totals_cells}"
        "</tr>"
    )

    thead = (
        "<thead><tr>"
        "<th>Баркод</th><th>Артикул</th><th>Название</th><th>Тотал</th>"
        f"{header_cells}"
        "</tr>"
        f"{totals_row}"
        "</thead>"
    )

    body_rows = []
    for (barcode, article, name), by_warehouse in products.items():
        total = sum(by_warehouse.values())
        cells = "".join(
            f'<td class="col-other">{by_warehouse.get(w, "—")}</td>'
            if w == OTHER_WAREHOUSES_LABEL and other
            else f"<td>{by_warehouse.get(w, '—')}</td>"
            for w in warehouse_list
        )
        body_rows.append(
            "<tr>"
            f"<td>{html.escape(barcode)}</td>"
            f"<td>{html.escape(article)}</td>"
            f"<td>{html.escape(name)}</td>"
            f'<td class="col-total">{total}</td>'
            f"{cells}"
            "</tr>"
        )

    # Таблица всегда вписывается в ширину панели, без горизонтальной прокрутки.
    # Чем больше складов, тем теснее колонки — сколько их, знает только сервер,
    # поэтому класс плотности проставляем здесь, а не в CSS.
    density = ""
    if len(warehouse_list) > 14:
        density = " is-dense-2"
    elif len(warehouse_list) > 7:
        density = " is-dense"

    return (
        f'<table class="data-table data-table--warehouses{density}">'
        f"{thead}<tbody>{''.join(body_rows)}</tbody>"
        "</table>"
    )


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    content = read_template("index_content.html")
    return render_page("PAKETA — Снабжение и остатки", "", content, request.state.user)


@app.get("/supply", response_class=HTMLResponse)
async def supply(request: Request):
    content = read_template("supply_content.html")
    return render_page("PAKETA — Снабжение", "supply", content, request.state.user)


@app.get("/stock", response_class=HTMLResponse)
async def stock(request: Request):
    content = fill_template(
        "stock_content.html",
        last_sync=html.escape(format_dt(db.get_last_sync_at())),
    )
    return render_page("PAKETA — Остатки", "stock", content, request.state.user)


@app.get("/stock/{slug}", response_class=HTMLResponse)
async def stock_store(request: Request, slug: str, mp: str = ""):
    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    # Маркетплейс выбирается на сервере: при переключении вкладки страница
    # перезагружается целиком. Так все колонки, итоги и подсветка гарантированно
    # относятся к одному маркетплейсу — частичное обновление тут легко
    # рассинхронизировать.
    marketplace = mp if mp in db.MARKETPLACES else db.DEFAULT_MARKETPLACE
    content = fill_template(
        "store_content.html",
        store_name=store["name"],
        store_color=store["color"],
        store_text=store["text"],
        store_initials=store["initials"],
        slug=slug.lower(),
        ff_options=render_ff_options(),
        mp_tabs=render_mp_tabs(marketplace, slug.lower()),
        mp_move_options=render_mp_move_options(),
        marketplace=html.escape(marketplace),
        mp_ready="1" if marketplace_ready(marketplace, slug.lower()) else "0",
        can_edit_stock="1" if auth.can_edit_stock(request.state.user) else "0",
        access_problems=html.escape(json.dumps(
            health.store_problems(slug.lower()), ensure_ascii=False)),
        stock_head=render_stock_head(marketplace, slug.lower()),
        scheme_list=",".join(scheme for scheme, _ in schemes_for(marketplace, slug.lower())),
        scheme_count=str(len(schemes_for(marketplace, slug.lower()))),
        stock_rows=render_stock_rows(slug.lower(), marketplace),
        stock_totals=render_stock_totals(slug.lower(), marketplace),
    )
    return render_page(f"PAKETA — Остатки — {store['name']}", "stock", content, request.state.user)


@app.get("/stock/{slug}/fbs")
async def stock_store_fbs_by_ff(slug: str, ff: str = "", mp: str = ""):
    """Остатки FBS для колонки на странице магазина: по конкретному складу
    (?ff=Название) или тотал по всем складам сразу.

    mp обязателен по смыслу: без него отдали бы остатки не того маркетплейса.
    Если не передан — берём тот, что показывается по умолчанию.
    """
    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    marketplace = mp or db.DEFAULT_MARKETPLACE
    if ff:
        stock = db.get_mp_stock_by_warehouse(slug.lower(), marketplace, "fbs", ff)
    else:
        stock = db.get_mp_stock_totals(slug.lower(), marketplace, "fbs")

    return JSONResponse({"fbs": stock})


@app.get("/stock/{slug}/ff-available")
async def stock_store_ff_available(slug: str, ff: str = "", mp: str = ""):
    """Остатки "Доступно ФФ для распределения" по товару: по конкретному
    фулфилменту (?ff=Название) или "Общее" по всем ФФ сразу (без параметра ff).
    Используется и переключателем ФФ, и обновлением таблицы после ручной
    загрузки остатков — чтобы не перезагружать страницу."""
    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    return JSONResponse(
        {"ff_available": db.get_ff_available_totals(slug.lower(), ff or None, mp or None)}
    )


@app.post("/stock/{slug}/upload-ff-stock")
async def upload_ff_stock(
    request: Request,
    slug: str,
    fulfillment: str = Form(...),
    marketplace: str = Form(db.DEFAULT_MARKETPLACE),
    sheet_url: str = Form(""),
    file: UploadFile | None = File(None),
):
    """Ручная загрузка остатков "Доступно ФФ для распределения" — либо файлом
    .xlsx, либо ссылкой на публичную Google Таблицу с колонками BARCODE/ARTICLE/WB."""
    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    denied = _guard_stock_edit(request.state.user)
    if denied:
        return JSONResponse({"ok": False, "error": denied}, status_code=403)

    fulfillment = fulfillment.strip()
    if not fulfillment:
        return JSONResponse({"ok": False, "error": "Выберите фулфилмент назначения"}, status_code=400)

    # Маркетплейс — тот, на вкладке которого работают. Неизвестное значение не
    # подменяем молча на WB: остаток, уехавший не на ту площадку, потом ищут
    # руками по всему каталогу.
    if marketplace not in db.MARKETPLACES:
        return JSONResponse({"ok": False, "error": "неизвестный маркетплейс"}, status_code=400)

    file_bytes = await file.read() if (file is not None and file.filename) else None
    source_type, source_name = _source_of(file, file_bytes, sheet_url)
    label = source_name or sheet_url.strip() or "ручной ввод"

    # Вид источника включает площадку: тот же файл, загруженный на вкладке WB
    # и на вкладке Ozon, — две разные поставки, а не повтор.
    source_kind = f"delivery:{marketplace}"
    fingerprint, used_error = await run_in_threadpool(
        _guard_used_source, slug.lower(), source_kind, source_type,
        sheet_url, file_bytes, label,
    )
    if used_error:
        return JSONResponse({"ok": False, "error": used_error}, status_code=400)

    try:
        if file_bytes is not None:
            report = await run_in_threadpool(
                ff_stock_import.import_ff_stock_from_xlsx, slug.lower(), fulfillment,
                file_bytes, file.filename, marketplace,
            )
        elif sheet_url.strip():
            report = await run_in_threadpool(
                ff_stock_import.import_ff_stock_from_sheet, slug.lower(), fulfillment,
                sheet_url.strip(), marketplace,
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
            store_slug=slug.lower(), kind="delivery",
            source_type=source_type,
            items=report.get("items", []),
            user_id=actor["id"], user_name=actor["full_name"], created_at=now,
            source_name=report.get("table_title"),
            sheet_url=sheet_url.strip() or None,
            to_fulfillment=fulfillment, to_marketplace=marketplace,
        )
        db.log_action_for_operation(
            actor["id"], actor["full_name"], "Загружена поставка на ФФ",
            f'{store["name"]} · {marketplace} · {fulfillment} · «{report["table_title"]}» — '
            f'обновлено {report["matched"]} из {report["total_rows"]} строк',
            now, operation_id,
        )
        db.record_used_source(
            slug.lower(), source_kind, fingerprint,
            report.get("table_title") or label, source_type,
            operation_id, actor["full_name"], now,
        )

    await run_in_threadpool(_record)
    return JSONResponse({"ok": True, "report": report})


def _guard_user_action(actor: dict, target: dict | None, what: str) -> str | None:
    """Общие проверки для операций над сотрудником.
    Возвращает текст ошибки или None, если всё можно."""
    if target is None:
        return "сотрудник не найден"
    if target["id"] == actor["id"]:
        return f"нельзя {what} самого себя"
    if not can_manage_user(actor, target):
        return (
            f"{what} можно только обычных пользователей — "
            "админов и суперадминов трогает суперадмин"
        )
    return None


@app.post("/admin/users/{user_id}/delete")
async def admin_delete_user(request: Request, user_id: int):
    actor = request.state.user
    if not auth.has_role(actor, "admin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    target = await run_in_threadpool(db.get_user, user_id)
    error = _guard_user_action(actor, target, "удалить")
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=400)

    # последнего суперадмина удалять нельзя — иначе в админку будет не войти
    if target["role"] == "superadmin":
        left = await run_in_threadpool(db.count_superadmins, user_id)
        if left == 0:
            return JSONResponse(
                {"ok": False, "error": "это последний суперадмин — сначала назначьте другого"},
                status_code=400,
            )

    def _delete() -> None:
        with db.WRITE_LOCK:
            db.delete_user(user_id)
            db.log_action(
                actor["id"], actor["full_name"], "Удалён сотрудник",
                f'{target["full_name"]} ({target["login"]})', _now_iso(),
            )

    await run_in_threadpool(_delete)
    return JSONResponse({"ok": True})


@app.post("/admin/users/{user_id}/reset-password")
async def admin_reset_password(request: Request, user_id: int, password: str = Form(...)):
    """Сброс пароля забывшему сотруднику. Новый пароль генерирует админ в
    интерфейсе — сервер только хеширует его и разлогинивает пользователя
    со всех устройств."""
    actor = request.state.user
    if not auth.has_role(actor, "admin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    target = await run_in_threadpool(db.get_user, user_id)
    error = _guard_user_action(actor, target, "сбросить пароль")
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=400)
    if len(password) < 8:
        return JSONResponse({"ok": False, "error": "пароль минимум 8 символов"}, status_code=400)

    def _reset() -> None:
        with db.WRITE_LOCK:
            db.update_user_password(user_id, auth.hash_password(password))
            db.log_action(
                actor["id"], actor["full_name"], "Сброшен пароль сотрудника",
                f'{target["full_name"]} ({target["login"]})', _now_iso(),
            )

    await run_in_threadpool(_reset)
    return JSONResponse({"ok": True})


@app.post("/admin/users/{user_id}/toggle-stock-edit")
async def admin_toggle_stock_edit(request: Request, user_id: int):
    """Разрешить или запретить сотруднику менять остатки.

    Отдельная кнопка, а не смена роли: роль описывает должность и меняется
    редко, а допуск к операциям — состояние, которое включают по мере того,
    как человек освоился.
    """
    actor = request.state.user
    target = await run_in_threadpool(db.get_user, user_id)

    error = _guard_user_action(actor, target, "менять права")
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=403)

    allowed = not auth.can_edit_stock(target)

    def _apply() -> None:
        db.set_user_permission(user_id, "can_edit_stock", allowed)
        db.log_action(
            actor["id"], actor["full_name"],
            "Разрешены изменения остатков" if allowed else "Запрещены изменения остатков",
            target["full_name"], _now_iso(),
        )

    await run_in_threadpool(_apply)
    return JSONResponse({"ok": True, "allowed": allowed})


@app.post("/admin/users/{user_id}/toggle-active")
async def admin_toggle_active(request: Request, user_id: int):
    """Блокировка/разблокировка — мягкая альтернатива удалению: сотрудник
    теряет доступ, но история и учётка остаются."""
    actor = request.state.user
    if not auth.has_role(actor, "admin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    target = await run_in_threadpool(db.get_user, user_id)
    error = _guard_user_action(actor, target, "заблокировать")
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=400)

    new_state = not target["is_active"]
    if not new_state and target["role"] == "superadmin":
        left = await run_in_threadpool(db.count_superadmins, user_id)
        if left == 0:
            return JSONResponse(
                {"ok": False, "error": "это последний суперадмин — сначала назначьте другого"},
                status_code=400,
            )

    def _toggle() -> None:
        with db.WRITE_LOCK:
            db.set_user_active(user_id, new_state)
            if not new_state:
                db.delete_sessions_for_user(user_id)
            db.log_action(
                actor["id"], actor["full_name"],
                "Разблокирован сотрудник" if new_state else "Заблокирован сотрудник",
                f'{target["full_name"]} ({target["login"]})', _now_iso(),
            )

    await run_in_threadpool(_toggle)
    return JSONResponse({"ok": True, "is_active": new_state})


@app.get("/stock/{slug}/catalog-search")
async def catalog_search(slug: str, q: str = "", ff: str = "", mp: str = ""):
    """Подсказки при вводе позиции: ищем по артикулу, баркоду и названию.

    Если переданы ff и mp, отдаём только товары, которые лежат в этой ячейке
    (для формы перемещения), вместе с доступным количеством.
    """
    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    items = await run_in_threadpool(
        db.search_catalog, slug.lower(), q, 15, ff or None, mp or None
    )
    return JSONResponse({"items": items})


@app.post("/stock/{slug}/add-ff-items")
async def add_ff_items(request: Request, slug: str):
    """Ручная докладка нескольких позиций на ФФ.

    Тело — JSON: {"fulfillment": "...", "items": [{"code": "...", "quantity": 1}, ...]}
    """
    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    denied = _guard_stock_edit(request.state.user)
    if denied:
        return JSONResponse({"ok": False, "error": denied}, status_code=403)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "неверный формат запроса"}, status_code=400)

    fulfillment = str(payload.get("fulfillment") or "").strip()
    if not fulfillment:
        return JSONResponse({"ok": False, "error": "Выберите фулфилмент назначения"}, status_code=400)

    marketplace = str(payload.get("marketplace") or db.DEFAULT_MARKETPLACE).strip()
    if marketplace not in db.MARKETPLACES:
        return JSONResponse({"ok": False, "error": "неизвестный маркетплейс"}, status_code=400)

    items = payload.get("items")
    if not isinstance(items, list):
        return JSONResponse({"ok": False, "error": "не переданы позиции"}, status_code=400)

    try:
        results = await run_in_threadpool(
            ff_stock_import.add_items, slug.lower(), fulfillment, items, marketplace
        )
    except ff_stock_import.FFImportError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception:
        logger.exception("Ручная докладка на ФФ (%s, %s) упала", slug, fulfillment)
        return JSONResponse({"ok": False, "error": "непредвиденная ошибка — см. лог сервера"}, status_code=500)

    actor = request.state.user
    details = ", ".join(f'{r["article"]} +{r["added"]}' for r in results)

    def _record() -> None:
        now = _now_iso()
        operation_id = db.record_operation(
            store_slug=slug.lower(), kind="manual_add", source_type="manual",
            items=[
                {"article": r["article"], "barcode": r.get("barcode"),
                 "name": r["name"], "quantity": r["added"]}
                for r in results
            ],
            user_id=actor["id"], user_name=actor["full_name"], created_at=now,
            to_fulfillment=fulfillment, to_marketplace=marketplace,
        )
        db.log_action_for_operation(
            actor["id"], actor["full_name"], "Добавлен остаток на ФФ вручную",
            f'{store["name"]} · {fulfillment} · {details}', now, operation_id,
        )

    await run_in_threadpool(_record)

    return JSONResponse({"ok": True, "results": results})


@app.get("/stock/{slug}/ff-cell")
async def ff_cell_stock(slug: str, ff: str = "", mp: str = ""):
    """Остатки в конкретной ячейке (ФФ + маркетплейс) — чтобы в форме
    перемещения сразу показывать, сколько есть на источнике."""
    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if not ff or not mp:
        return JSONResponse({"stock": {}})
    stock = await run_in_threadpool(db.get_ff_available_totals, slug.lower(), ff, mp)
    return JSONResponse({"stock": stock})


def _guard_stock_edit(actor: dict) -> str | None:
    """Есть ли у сотрудника допуск к изменению остатков.

    Проверяем на сервере, а не только прячем кнопку: кнопка — это удобство,
    а запрет должен работать и для запроса, отправленного мимо интерфейса.
    """
    if auth.can_edit_stock(actor):
        return None
    return ("изменение остатков для вашей учётной записи пока закрыто — "
            "обратитесь к администратору")


def _source_of(file: UploadFile | None, file_bytes: bytes | None,
               sheet_url: str) -> tuple[str, str | None]:
    """Как пришли позиции: файлом, ссылкой или руками."""
    if file is not None and file.filename:
        return "file", file.filename
    if sheet_url.strip():
        return "sheet", None
    return "manual", None


def _download_headers(filename: str) -> dict:
    """Заголовок скачивания, переживающий кириллицу в имени файла.

    В filename= по стандарту допустим только ASCII, поэтому туда кладём
    очищенный вариант, а настоящее имя отдаём в filename* — его понимают все
    актуальные браузеры. Без этого файл «Остатки Чувашия.xlsx» сохранялся бы
    с покорёженным именем, а часть серверов вообще отвергла бы заголовок.
    """
    ascii_name = filename.encode("ascii", "ignore").decode("ascii").strip() or "export.xlsx"
    quoted = urllib.parse.quote(filename)
    return {"Content-Disposition":
            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'}


def _guard_used_source(store_slug: str, kind: str, source_type: str,
                       sheet_url: str, file_bytes: bytes | None,
                       label: str) -> tuple[str | None, str | None]:
    """Проверяет, не проводили ли уже этот файл/ссылку.

    Возвращает (отпечаток, текст ошибки). Отпечаток нужен вызывающему коду,
    чтобы записать источник использованным ПОСЛЕ успеха — если операция
    упадёт, файл должен остаться доступным для повторной попытки.
    """
    fingerprint = db.source_fingerprint(source_type, sheet_url.strip() or None, file_bytes)
    used = db.find_used_source(store_slug, kind, fingerprint)
    if used is None:
        return fingerprint, None

    what = "Эта ссылка" if source_type == "sheet" else "Этот файл"
    return fingerprint, (
        f'{what} уже проводили {format_dt(used["created_at"])}'
        + (f' — {used["user_name"]}' if used.get("user_name") else "")
        + ". Повторное проведение запрещено, чтобы не начислить или не списать дважды."
        + (f' Тогда источник назывался «{used["label"]}».'
           if used.get("label") and used["label"] != label else "")
    )


@app.post("/stock/{slug}/transfer")
async def transfer_ff_stock(
    request: Request,
    slug: str,
    from_fulfillment: str = Form(...),
    from_marketplace: str = Form(...),
    to_fulfillment: str = Form(...),
    to_marketplace: str = Form(...),
    items: str = Form(""),
    sheet_url: str = Form(""),
    file: UploadFile | None = File(None),
):
    """Перемещение остатков между фулфилментами и/или маркетплейсами.

    Позиции приходят либо списком JSON в поле items (ручной ввод),
    либо файлом .xlsx, либо ссылкой на Google Таблицу.
    """
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

    # Площадка-источник входит в вид источника: перемещение того же списка
    # товаров с WB и с Ozon — разные операции по разным ячейкам склада.
    source_kind = f"transfer:{from_marketplace}"
    fingerprint, used_error = await run_in_threadpool(
        _guard_used_source, slug.lower(), source_kind, source_type,
        sheet_url, file_bytes, label,
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
                raw_entries = json.loads(items or "[]")
            except ValueError:
                return JSONResponse({"ok": False, "error": "неверный формат позиций"}, status_code=400)
            if not isinstance(raw_entries, list):
                return JSONResponse({"ok": False, "error": "неверный формат позиций"}, status_code=400)

        results = await run_in_threadpool(
            ff_transfer.transfer,
            slug.lower(), raw_entries,
            from_fulfillment.strip(), from_marketplace.strip(),
            to_fulfillment.strip(), to_marketplace.strip(),
            actor["id"], actor["full_name"],
        )
    except ff_stock_import.FFImportError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception:
        logger.exception("Перемещение остатков (%s) упало", slug)
        return JSONResponse({"ok": False, "error": "непредвиденная ошибка — см. лог сервера"}, status_code=500)

    moved = ", ".join(f'{r["article"]} x{r["quantity"]}' for r in results)

    def _record() -> None:
        now = _now_iso()
        operation_id = db.record_operation(
            store_slug=slug.lower(), kind="transfer", source_type=source_type,
            items=results,
            user_id=actor["id"], user_name=actor["full_name"], created_at=now,
            source_name=source_name, sheet_url=sheet_url.strip() or None,
            from_fulfillment=from_fulfillment.strip(), from_marketplace=from_marketplace.strip(),
            to_fulfillment=to_fulfillment.strip(), to_marketplace=to_marketplace.strip(),
        )
        db.log_action_for_operation(
            actor["id"], actor["full_name"], "Перемещение между фулфилментами",
            f'{store["name"]} · {from_fulfillment}/{from_marketplace} -> '
            f'{to_fulfillment}/{to_marketplace} · {moved}',
            now, operation_id,
        )
        # источник помечаем использованным только сейчас, когда перемещение
        # уже проведено: упавшую попытку надо иметь возможность повторить
        db.record_used_source(
            slug.lower(), source_kind, fingerprint, label, source_type,
            operation_id, actor["full_name"], now,
        )

    await run_in_threadpool(_record)

    return JSONResponse({"ok": True, "results": results})


@app.post("/stock/{slug}/shipment")
async def ship_ff_stock(
    request: Request,
    slug: str,
    fulfillment: str = Form(...),
    marketplace: str = Form(...),
    note: str = Form(""),
    to_trash: str = Form(""),
    items: str = Form(""),
    sheet_url: str = Form(""),
    file: UploadFile | None = File(None),
):
    """Отгрузка стока с фулфилмента — товар уходит со склада наружу.

    to_trash — особый случай: товар не отгружен, а не найден на складе.
    Тогда он не исчезает, а перекладывается в мусорку.

    Позиции приходят либо списком JSON в поле items (ручной ввод), либо
    файлом .xlsx, либо ссылкой на Google Таблицу — как и в перемещении.
    """
    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    actor = request.state.user
    denied = _guard_stock_edit(actor)
    if denied:
        return JSONResponse({"ok": False, "error": denied}, status_code=403)

    trash = to_trash.strip().lower() in ("1", "true", "on", "yes")
    kind = "trash" if trash else "shipment"
    # тот же файл может относиться к разным площадкам — см. transfer выше
    source_kind = f"{kind}:{marketplace}"

    file_bytes = await file.read() if (file is not None and file.filename) else None
    source_type, source_name = _source_of(file, file_bytes, sheet_url)
    label = source_name or sheet_url.strip() or "ручной ввод"

    fingerprint, used_error = await run_in_threadpool(
        _guard_used_source, slug.lower(), source_kind, source_type,
        sheet_url, file_bytes, label,
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
                raw_entries = json.loads(items or "[]")
            except ValueError:
                return JSONResponse({"ok": False, "error": "неверный формат позиций"}, status_code=400)
            if not isinstance(raw_entries, list):
                return JSONResponse({"ok": False, "error": "неверный формат позиций"}, status_code=400)

        results = await run_in_threadpool(
            ff_shipment.ship,
            slug.lower(), raw_entries,
            fulfillment.strip(), marketplace.strip(), trash,
        )
    except ff_stock_import.FFImportError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception:
        logger.exception("Отгрузка стока (%s) упала", slug)
        return JSONResponse({"ok": False, "error": "непредвиденная ошибка — см. лог сервера"}, status_code=500)

    shipped = ", ".join(f'{r["article"]} x{r["quantity"]}' for r in results)
    note_text = note.strip()

    def _record() -> None:
        now = _now_iso()
        operation_id = db.record_operation(
            store_slug=slug.lower(), kind=kind, source_type=source_type,
            items=results,
            user_id=actor["id"], user_name=actor["full_name"], created_at=now,
            source_name=source_name, sheet_url=sheet_url.strip() or None,
            from_fulfillment=fulfillment.strip(), from_marketplace=marketplace.strip(),
            to_fulfillment="Мусорка" if trash else None,
            to_marketplace=marketplace.strip() if trash else None,
            note=note_text or None,
        )
        db.log_action_for_operation(
            actor["id"], actor["full_name"],
            "Списание в мусорку" if trash else "Отгрузка стока",
            f'{store["name"]} · {fulfillment}/{marketplace} · {shipped}'
            + (f' · {note_text}' if note_text else ""),
            now, operation_id,
        )
        db.record_used_source(
            slug.lower(), source_kind, fingerprint, label, source_type,
            operation_id, actor["full_name"], now,
        )

    await run_in_threadpool(_record)

    return JSONResponse({"ok": True, "results": results})


# Какие движения показываем в истории и в каком порядке идут вкладки.
# Ручная докладка — тоже движение стока, поэтому она здесь, а не спрятана:
# иначе цифры в истории не сходились бы с фактическим остатком.
OPERATION_FILTERS = [
    ("", "Все"),
    ("delivery", "Поставки"),
    ("transfer", "Перемещения"),
    ("shipment", "Отгрузки"),
    ("manual_add", "Ручные докладки"),
]

HISTORY_LIMIT = 500


def render_kind_tabs(slug: str, active: str) -> str:
    parts = []
    for kind, label in OPERATION_FILTERS:
        cls = "ops-filter active" if kind == active else "ops-filter"
        href = f"/stock/{slug}/operations" + (f"?kind={kind}" if kind else "")
        parts.append(f'<a class="{cls}" href="{href}">{html.escape(label)}</a>')
    return "\n".join(parts)


def render_operation_rows(store_slug: str, kinds: tuple[str, ...] | None) -> str:
    operations = db.get_store_operations(store_slug, kinds, HISTORY_LIMIT)
    if not operations:
        return '<tr class="empty-row"><td colspan="9">Движений пока не было</td></tr>'

    def cell(fulfillment, marketplace) -> str:
        parts = [p for p in (fulfillment, marketplace) if p]
        return html.escape(" / ".join(parts)) if parts else '<span class="u-note">—</span>'

    rows = []
    for op in operations:
        note = op.get("note") or ""
        rows.append(
            "<tr>"
            f'<td data-label="Когда">{html.escape(format_dt(op["created_at"]))}</td>'
            f'<td data-label="Тип">{html.escape(db.OPERATION_LABELS.get(op["kind"], op["kind"]))}</td>'
            f'<td data-label="Откуда">{cell(op["from_fulfillment"], op["from_marketplace"])}</td>'
            f'<td data-label="Куда">{cell(op["to_fulfillment"], op["to_marketplace"])}</td>'
            f'<td data-label="Позиций" class="u-num">{op["positions"]}</td>'
            f'<td data-label="Единиц" class="u-num">{_fmt_num(op["units"])}</td>'
            f'<td data-label="Примечание">{html.escape(note)}</td>'
            f'<td data-label="Сотрудник">{html.escape(op["user_name"])}</td>'
            f'<td data-label="Файл">'
            f'<a class="log-download" href="/admin/operations/{op["id"]}/xlsx" '
            f'title="Скачать файл операции">xlsx</a></td>'
            "</tr>"
        )
    return "".join(rows)


def _history_kinds(kind: str) -> tuple[str, ...] | None:
    """Пустой фильтр значит «все виды», а не «ни одного»."""
    kind = (kind or "").strip()
    known = {k for k, _ in OPERATION_FILTERS if k}
    return (kind,) if kind in known else None


@app.get("/stock/{slug}/operations", response_class=HTMLResponse)
async def stock_store_operations(request: Request, slug: str, kind: str = ""):
    """История движений стока магазина — отдельной страницей, потому что
    журнал действий в админке общий на всех и по нему неудобно смотреть
    один магазин."""
    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    kinds = _history_kinds(kind)
    active = kinds[0] if kinds else ""

    content = fill_template(
        "operations_content.html",
        slug=slug.lower(),
        store_name=store["name"],
        store_color=store["color"],
        store_initials=store["initials"],
        store_text=store["text"],
        limit=str(HISTORY_LIMIT),
        kind=active,
        kind_tabs=render_kind_tabs(slug.lower(), active),
        rows=await run_in_threadpool(render_operation_rows, slug.lower(), kinds),
    )
    return render_page(
        f"PAKETA — Перемещение стока — {store['name']}", "stock", content, request.state.user
    )


@app.get("/stock/{slug}/operations/xlsx")
async def stock_store_operations_xlsx(slug: str, kind: str = ""):
    """Вся показанная история одним файлом: лист операций и лист позиций."""
    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    kinds = _history_kinds(kind)

    def _build():
        operations = db.get_operations_with_items(slug.lower(), kinds, HISTORY_LIMIT)
        return ff_export.build_history_xlsx(slug.lower(), store["name"], operations)

    try:
        content, filename = await run_in_threadpool(_build)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )


def _warehouse_tables(store_slug: str, marketplace: str) -> list[tuple[str, list[dict]]]:
    """Все вкладки детализации складов одним списком — и для страницы,
    и для выгрузки, чтобы они не разошлись между собой."""
    return [
        (f"FBO {marketplace}", db.get_mp_warehouse_details(store_slug, marketplace, "fbo")),
        ("FBS склады продавца", db.get_mp_warehouse_details(store_slug, marketplace, "fbs")),
        ("ФФ фулфилменты", db.get_ff_warehouse_details_by_mp(store_slug, marketplace)),
        ("Мусорка", db.get_trash_details(store_slug, marketplace)),
    ]


@app.get("/stock/{slug}/stock.xlsx")
async def stock_store_xlsx(slug: str, mp: str = "", ff: str = ""):
    """Основная таблица остатков магазина в .xlsx — ровно то, что на экране.

    Учитываем выбранный склад: при выборе ФФ страница показывает остаток этого
    склада и его же FBS, и выгрузка должна совпадать с тем, что видит человек.
    Иначе файл и экран разойдутся, а доверять после этого будут файлу.
    """
    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    marketplace = mp if mp in db.MARKETPLACES else db.DEFAULT_MARKETPLACE
    store_slug = slug.lower()
    schemes = schemes_for(marketplace, store_slug)

    def _build():
        items = db.get_stock_items(store_slug, marketplace, tuple(k for k, _ in schemes))

        # переключаемые колонки — как в интерфейсе (см. refresh() на странице)
        ff_map = db.get_ff_available_totals(store_slug, ff or None, marketplace)
        fbs_map = (db.get_mp_stock_by_warehouse(store_slug, marketplace, "fbs", ff)
                   if ff else None)

        columns = ["АРТИКУЛ", "ШТРИХКОД", "НАЗВАНИЕ", "ТОТАЛ",
                   "ДОСТУПНО ФФ ДЛЯ РАСПРЕДЕЛЕНИЯ"]
        columns += [title.upper() for _scheme, title in schemes]

        rows = []
        totals = [0] * len(columns)
        for item in items:
            article = item["article"]
            ff_available = ff_map.get(article, 0) or 0

            by_scheme = []
            for scheme, _title in schemes:
                if scheme == "fbs" and fbs_map is not None:
                    by_scheme.append(fbs_map.get(article, 0) or 0)
                else:
                    by_scheme.append(item[f"{scheme}_stock"] or 0)

            row_total = ff_available + sum(by_scheme)
            rows.append([article, item["barcode"], item["name"],
                         row_total, ff_available, *by_scheme])

            for index, value in enumerate([row_total, ff_available, *by_scheme], start=3):
                totals[index] += value

        totals[0] = "ИТОГО"
        totals[1] = ""
        totals[2] = f"позиций: {len(rows)}"

        return ff_export.build_stock_xlsx(
            store_slug, store["name"], marketplace, columns, rows, totals, ff,
        )

    try:
        content, filename = await run_in_threadpool(_build)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )


@app.get("/stock/{slug}/warehouses/xlsx")
async def stock_store_warehouses_xlsx(slug: str, mp: str = ""):
    """Все таблицы детализации складов в одном файле, лист на вкладку."""
    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    marketplace = mp or db.DEFAULT_MARKETPLACE

    def _build():
        tables = _warehouse_tables(slug.lower(), marketplace)
        return ff_export.build_warehouses_xlsx(slug.lower(), store["name"], marketplace, tables)

    try:
        content, filename = await run_in_threadpool(_build)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )


@app.post("/stock/{slug}/trash/checked")
async def toggle_trash_checked(
    request: Request,
    slug: str,
    marketplace: str = Form(...),
    article: str = Form(...),
    fulfillment: str = Form(...),
    checked: str = Form(""),
):
    """Отметка «разобрались» у позиции мусорки."""
    if slug.lower() not in STORES:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    denied = _guard_stock_edit(request.state.user)
    if denied:
        return JSONResponse({"ok": False, "error": denied}, status_code=403)

    value = checked.strip().lower() in ("1", "true", "on", "yes")
    await run_in_threadpool(
        db.set_trash_checked, slug.lower(), marketplace.strip(),
        article.strip(), fulfillment.strip(), value,
    )
    return JSONResponse({"ok": True, "checked": value})


@app.get("/stock/{slug}/warehouses", response_class=HTMLResponse)
async def stock_store_warehouses(request: Request, slug: str, mp: str = ""):
    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    marketplace = mp or db.DEFAULT_MARKETPLACE
    content = fill_template(
        "warehouse_content.html",
        store_name=store["name"],
        store_color=store["color"],
        store_text=store["text"],
        store_initials=store["initials"],
        slug=slug.lower(),
        marketplace=html.escape(marketplace),
        # Склады FBO сворачиваем до топ-8: у Ozon их 45, и таблица во всю
        # ширину экрана становится нечитаемой. У WB складов около двадцати,
        # порог тот же — правило одно, а не «для Ozon особый случай».
        fbo_table=render_warehouse_table(
            db.get_mp_warehouse_details(slug.lower(), marketplace, "fbo"),
            f"Пока нет данных по складам {marketplace} — запустите синхронизацию на странице «Остатки»",
            top_n=TOP_WAREHOUSES,
        ),
        fbs_table=render_warehouse_table(
            db.get_mp_warehouse_details(slug.lower(), marketplace, "fbs"),
            "Пока нет данных по складам продавца — запустите синхронизацию на странице «Остатки»",
        ),
        ff_table=render_warehouse_table(
            db.get_ff_warehouse_details_by_mp(slug.lower(), marketplace),
            "Пока нет остатков на фулфилментах — загрузите поставку на странице магазина",
        ),
        trash_table=render_trash_table(
            slug.lower(), marketplace, auth.can_edit_stock(request.state.user)
        ),
    )
    return render_page(f"PAKETA — Склады — {store['name']}", "stock", content, request.state.user)


def creatable_roles(actor: dict) -> list[str]:
    """Кого может заводить этот сотрудник.

    Суперадмин — кого угодно, включая других админов и суперадминов.
    Админ — только обычных пользователей.
    """
    if auth.has_role(actor, "superadmin"):
        return list(db.ROLES)
    if auth.has_role(actor, "admin"):
        return ["user"]
    return []


def can_manage_user(actor: dict, target: dict) -> bool:
    """Может ли actor распоряжаться учёткой target (сброс пароля, блокировка,
    удаление). Суперадмин — всеми, кроме себя. Админ — только обычными
    пользователями: админов и суперадминов он не трогает.

    Отдельно проверяем разрешение: у тестового стенда роль суперадмина, но
    живых сотрудников он не правит — смотрит админку и только."""
    if not auth.can_manage_users(actor):
        return False
    if not target or target["id"] == actor["id"]:
        return False
    if auth.has_role(actor, "superadmin"):
        return True
    if auth.has_role(actor, "admin"):
        return target["role"] == "user"
    return False


def render_role_options(actor: dict) -> str:
    return "\n".join(
        f'                    <option value="{r}">{html.escape(db.ROLE_LABELS[r])}</option>'
        for r in creatable_roles(actor)
    )


def render_user_rows(actor: dict) -> str:
    users = db.list_users()
    if not users:
        return '<tr class="empty-row"><td colspan="7">Пока нет сотрудников</td></tr>'
    rows = []
    for u in users:
        active = bool(u["is_active"])
        status = (
            '<span class="u-status u-status--on">активен</span>' if active
            else '<span class="u-status u-status--off">заблокирован</span>'
        )
        # кнопки показываем только тем, кем этот сотрудник вправе распоряжаться:
        # админ видит действия лишь у обычных пользователей, себя не трогает никто
        can_edit = auth.can_edit_stock(u)
        edit_status = (
            '<span class="u-status u-status--on">разрешены</span>' if can_edit
            else '<span class="u-status u-status--off">запрещены</span>'
        )
        if can_manage_user(actor, u):
            actions = (
                f'<div class="u-actions" data-user-id="{u["id"]}" '
                f'data-user-name="{html.escape(u["full_name"], quote=True)}" '
                f'data-active="{"1" if active else "0"}" '
                f'data-can-edit="{"1" if can_edit else "0"}">'
                '<button type="button" class="u-act u-act--reset">Сбросить пароль</button>'
                f'<button type="button" class="u-act u-act--toggle">{"Заблокировать" if active else "Разблокировать"}</button>'
                f'<button type="button" class="u-act u-act--stock">'
                f'{"Запретить изменения" if can_edit else "Разрешить изменения"}</button>'
                '<button type="button" class="u-act u-act--delete">Удалить</button>'
                "</div>"
            )
        elif u["id"] == actor["id"]:
            actions = '<span class="u-note">это вы</span>'
        else:
            actions = '<span class="u-note">—</span>'

        rows.append(
            "<tr>"
            f'<td>{html.escape(u["full_name"])}</td>'
            f'<td>{html.escape(u["google_email"])}</td>'
            f'<td>{html.escape(u["login"])}</td>'
            f'<td>{html.escape(db.ROLE_LABELS.get(u["role"], u["role"]))}</td>'
            f"<td>{status}</td>"
            f"<td>{edit_status}</td>"
            f"<td>{actions}</td>"
            "</tr>"
        )
    return "".join(rows)


def render_log_rows() -> str:
    entries = db.get_activity_log()
    if not entries:
        return '<tr class="empty-row"><td colspan="5">Пока пусто</td></tr>'
    rows = []
    for e in entries:
        operation_id = e.get("operation_id")
        file_cell = (
            f'<a class="log-download" href="/admin/operations/{operation_id}/xlsx" '
            f'title="Скачать файл операции">xlsx</a>'
            if operation_id else '<span class="u-note">—</span>'
        )
        rows.append(
            "<tr>"
            f'<td data-label="Когда">{html.escape(format_dt(e["created_at"]))}</td>'
            f'<td data-label="Сотрудник">{html.escape(e["user_name"])}</td>'
            f'<td data-label="Действие">{html.escape(e["action"])}</td>'
            f'<td data-label="Подробности">{html.escape(e["details"] or "")}</td>'
            f'<td data-label="Файл">{file_cell}</td>'
            "</tr>"
        )
    return "".join(rows)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    user = request.state.user
    if not auth.has_role(user, "admin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    # Форма создания видна всем, кто дошёл до админки, но у кого нет права
    # управлять сотрудниками — она отключена. Прятать её нельзя: тогда
    # непонятно, чего не хватает, и человек идёт спрашивать.
    read_only = not auth.can_manage_users(user)
    content = fill_template(
        "admin_content.html",
        role_options=render_role_options(user),
        user_rows=render_user_rows(user),
        log_rows=render_log_rows(),
        create_hint=(
            '<p class="panel-desc panel-desc--warn">Режим просмотра: '
            "создание и изменение сотрудников недоступно.</p>"
            if read_only else ""
        ),
        form_disabled=" disabled" if read_only else "",
    )
    return render_page("PAKETA — Админка", "admin", content, user)


@app.get("/admin/operations/{operation_id}/xlsx")
async def download_operation(request: Request, operation_id: int):
    """Файл по операции собирается на лету из сохранённых строк —
    исходные файлы не хранятся."""
    if not auth.has_role(request.state.user, "admin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    try:
        content, filename = await run_in_threadpool(ff_export.build_operation_xlsx, operation_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Операция не найдена")
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )


@app.post("/admin/users")
async def admin_create_user(
    request: Request,
    full_name: str = Form(...),
    google_email: str = Form(...),
    login: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
):
    actor = request.state.user
    if not auth.has_role(actor, "admin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    full_name, google_email, login = full_name.strip(), google_email.strip(), login.strip()
    if not all([full_name, google_email, login, password, role]):
        return JSONResponse({"ok": False, "error": "заполните все поля"}, status_code=400)
    if role not in db.ROLES:
        return JSONResponse({"ok": False, "error": "неизвестная роль"}, status_code=400)
    if role not in creatable_roles(actor):
        return JSONResponse(
            {"ok": False, "error": "у вас нет прав заводить сотрудников с этой ролью"},
            status_code=403,
        )
    if len(password) < 8:
        return JSONResponse({"ok": False, "error": "пароль минимум 8 символов"}, status_code=400)
    if db.get_user_by_login(login) is not None:
        return JSONResponse({"ok": False, "error": "такой логин уже занят"}, status_code=400)

    def _create() -> None:
        with db.WRITE_LOCK:
            db.create_user(
                full_name, google_email, login,
                auth.hash_password(password), role, _now_iso(),
            )
            db.log_action(
                actor["id"], actor["full_name"], "Создан сотрудник",
                f"{full_name} ({login}), роль: {db.ROLE_LABELS.get(role, role)}", _now_iso(),
            )

    await run_in_threadpool(_create)
    return JSONResponse({"ok": True})


@app.post("/admin/sync-stock")
async def sync_stock():
    """Тянет остатки FBS и FBO с Wildberries по всем магазинам, у которых есть токен, и сохраняет в БД.
    Запросы к WB — блокирующие (urllib), поэтому выполняются в отдельном потоке,
    чтобы не морозить event loop сервера."""
    # Сначала каталог WB, потом остатки WB — по той же причине, что у Ozon
    # и Яндекса: остаток пишется только по товарам из каталога, поэтому
    # карточка, заведённая между синхронизациями, иначе ждала бы следующего раза.
    wb_catalog_report = await run_in_threadpool(wb_catalog.sync_all)
    report = await run_in_threadpool(wb_sync.sync_all)
    for slug, entry in wb_catalog_report.items():
        if slug in report:
            report[slug]["wb_catalog"] = entry

    # Ozon тянем следом, а не параллельно: это разные кабинеты, но лимиты
    # аналитических методов Ozon жёсткие, и лишняя нагрузка ни к чему.
    #
    # Сначала каталог, потом остатки. Порядок важен: остаток пишется только по
    # товарам, которые есть в каталоге, поэтому новая карточка, появившаяся на
    # Ozon между синхронизациями, иначе была бы пропущена до следующего раза.
    catalog_report = await run_in_threadpool(ozon_catalog.sync_all)
    ozon_report = await run_in_threadpool(ozon_sync.sync_all)
    for slug, entry in ozon_report.items():
        if slug in report:
            report[slug]["ozon"] = entry.get("ozon")
            report[slug]["ozon_token"] = entry.get("token")
            report[slug]["ozon_catalog"] = catalog_report.get(slug)

    # Яндекс идёт третьим по тому же принципу: сначала каталог, потом остатки.
    # Остаток пишется только по товарам из каталога, поэтому обратный порядок
    # терял бы всё, что завели на площадке между синхронизациями.
    ya_catalog_report = await run_in_threadpool(ya_catalog.sync_all)
    ya_report = await run_in_threadpool(ya_sync.sync_all)
    for slug, entry in ya_report.items():
        if slug in report:
            report[slug]["yandex"] = entry.get("yandex")
            report[slug]["yandex_token"] = entry.get("token")
            report[slug]["yandex_catalog"] = ya_catalog_report.get(slug)

    return JSONResponse({"report": report, "last_sync": format_dt(db.get_last_sync_at())})
