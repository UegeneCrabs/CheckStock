import json
from dataclasses import asdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse

from app.access.authentication import is_active_user
from app.access.resources import accessible_stores
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.dto.identity import coerce_user
from app.dto.spp_report import SppDirectionFilter
from app.economics.wb import spp_report
from app.economics.yandex.spp_report import YandexSppSource
from app.web.templating import fill_template, render_page

router = APIRouter()


def _stores(request: Request, marketplace: str = "WB") -> tuple[str, ...]:
    if not is_active_user(request.state.user):
        raise HTTPException(403, "Нет доступа к отчёту по СПП")
    return accessible_stores(request.state.user, marketplace)


def _period(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    today = datetime.now(MOSCOW_TIMEZONE).date()
    date_to = date_to or today
    date_from = date_from or date_to - timedelta(days=6)
    if date_from > date_to or date_to > today or date_from < date(2000, 1, 1):
        raise HTTPException(422, "Выберите корректный период до сегодняшнего дня")
    if (date_to - date_from).days >= 366:
        raise HTTPException(422, "Максимальный период отчёта — 366 дней")
    return date_from, date_to


def register_report(*, yandex: bool) -> None:
    marketplace = "YANDEX MARKET" if yandex else "WB"
    prefix = "/unit-economics-1c" + ("/yandex-market" if yandex else "") + "/reports/spp"
    page_url = "/sales" + prefix
    api_url = "/api" + prefix

    @router.get(page_url, response_class=HTMLResponse)
    async def page(request: Request):
        stores = _stores(request, marketplace)
        today = datetime.now(MOSCOW_TIMEZONE).date()
        config = {
            "dateFrom": today.isoformat(),
            "dateTo": today.isoformat(),
            "stores": [{"slug": store, "name": STORES[store]["name"]} for store in stores],
            "showManager": True,
            "showBrand": not yandex,
            "marketplace": marketplace,
            "apiBase": api_url,
            "storageKey": "checkstock:spp-report:yandex:period" if yandex else "checkstock:spp-report:period",
        }
        content = fill_template(
            "economics/shared/spp-report.html",
            spp_report_config=json.dumps(config, ensure_ascii=False).replace("</", "<\\/"),
        )
        return render_page(
            "CheckStock — Отчёт по СПП",
            "unit_1c_yandex_spp_report" if yandex else "unit_1c_spp_report",
            content,
            request.state.user,
            content_class="content--spp-report",
        )

    @router.get(api_url)
    async def data(
        request: Request,
        date_from: date | None = None,
        date_to: date | None = None,
        store: str = "",
        brand: str = "",
        manager: str = "",
        subject: str = "",
        article: str = "",
        search: str = "",
        direction: SppDirectionFilter = SppDirectionFilter.ALL,
        tolerance: float = Query(default=0, ge=0, allow_inf_nan=False),
        page: int = Query(default=1, ge=1),
        sort_by: spp_report.SppSortField = spp_report.SppSortField.TURNOVER,
        sort_order: spp_report.SppSortOrder = spp_report.SppSortOrder.DESC,
    ):
        stores = _stores(request, marketplace)
        source = YandexSppSource(coerce_user(request.state.user)) if yandex else spp_report
        date_from, date_to = _period(date_from, date_to)
        if store and store not in stores:
            raise HTTPException(403, "Нет доступа к кабинету")
        result = await run_in_threadpool(
            source.load,
            stores,
            date_from,
            date_to,
            spp_report.SppFilters(store, brand, manager, subject, article, search.strip(), direction),
            tolerance=tolerance,
            manager_scope=None,
            page=page,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        return asdict(result)

    @router.get(api_url + "/history")
    async def history(
        request: Request,
        store: str,
        article: str,
        period: spp_report.SppHistoryPeriod | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ):
        stores = _stores(request, marketplace)
        source = YandexSppSource(coerce_user(request.state.user)) if yandex else spp_report
        if period is not None and (date_from is not None or date_to is not None):
            raise HTTPException(422, "Укажите период графика или даты")
        if date_from is not None or date_to is not None:
            if date_from is None or date_to is None:
                raise HTTPException(422, "Укажите обе даты")
            date_from, date_to = _period(date_from, date_to)
        else:
            date_from, date_to = spp_report.history_dates(
                period or spp_report.SppHistoryPeriod.DAYS_7,
                datetime.now(MOSCOW_TIMEZONE).date(),
            )
        if store not in stores:
            raise HTTPException(403, "Нет доступа к кабинету")

        def authorized_history() -> tuple[spp_report.SppDailyPoint, ...] | None:
            for product in source._catalog((store,)):
                if product.article != article:
                    continue
                return source.daily_history(store, article, date_from, date_to)
            return None

        points = await run_in_threadpool(authorized_history)
        if points is None:
            raise HTTPException(404, "Товар не найден")
        return {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "days": [asdict(point) for point in points],
        }

    @router.get(api_url + "/hourly-history")
    async def hourly_history(request: Request, store: str, article: str, day: date | None = None):
        stores = _stores(request, marketplace)
        source = YandexSppSource(coerce_user(request.state.user)) if yandex else spp_report
        day = day or datetime.now(MOSCOW_TIMEZONE).date()
        _period(day, day)
        if store not in stores:
            raise HTTPException(403, "Нет доступа к кабинету")

        def authorized_history() -> spp_report.SppHourlyHistory | None:
            if any(product.article == article for product in source._catalog((store,))):
                return source.hourly_history(store, article, day)
            return None

        result = await run_in_threadpool(authorized_history)
        if result is None:
            raise HTTPException(404, "Товар не найден")
        return asdict(result)


register_report(yandex=False)
register_report(yandex=True)
