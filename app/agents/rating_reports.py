from typing import NamedTuple

from fastapi import HTTPException, Request
from pydantic import JsonValue

from app.agents import reports
from app.core.constants import AGENT_WB_RATING, WbRatingReportKind
from app.dto.agent_query import ReportQuery
from app.dto.agent_rating import RatingRequest
from app.dto.identity import SectionName


class RatingReportSpec(NamedTuple):
    section: SectionName
    marketplace_specific: bool
    filters: str
    description: str


SPECS = {
    WbRatingReportKind.COMPARISON: RatingReportSpec(
        SectionName.REPORT_RATING,
        True,
        "date_from date_to article search brand manager subject rating_below drop_at_least tolerance status",
        "WB joined-product-group rating comparison: last saved observation in each of two consecutive equal periods, not averages. Includes actual observation dates, separate latest cache, full filtered counts before pagination. Label every answer 'Рейтинг склейки WB'. Individual unjoined product rating is not stored. rating_below uses period rating, drop_at_least and tolerance are rating points; problem iff change < -tolerance. Missing period is unknown; default order change ascending. Not supported for Yandex or Ozon.",
    ),
    WbRatingReportKind.HISTORY: RatingReportSpec(
        SectionName.REPORT_RATING,
        True,
        "date_from date_to article",
        "WB daily saved joined-product-group rating and review-count history for one exact article/card. Dates required, max 90 days; returns every day, gaps remain null. observed_at is actual measurement time. Label 'Рейтинг склейки WB'; individual unjoined rating is not stored. No interpolation, backfill, live sync or averages. Default day ascending. Not supported for Yandex or Ozon.",
    ),
}


async def execute(
    name: str, request: Request, query: ReportQuery, *, paginate: bool = True
) -> dict[str, JsonValue]:
    from app.container import agent_rating_service

    fields = SPECS[WbRatingReportKind(name)].filters.split()
    typed_query = RatingRequest.model_validate(
        {
            "store": query.store,
            **query.model_dump(include=set(fields)),
        }
    )
    service = agent_rating_service(request.app.state.async_resources)
    context: dict[str, JsonValue] = {
        "rating_scope": AGENT_WB_RATING.scope,
        "rating_label": AGENT_WB_RATING.label,
        "individual_rating_available": False,
        "group_identity_available": False,
        "count_basis": "catalog_cards_not_distinct_joined_groups",
        "rating_unit": "rating_points_1_to_5",
        "date_filter_timezone": "Europe/Moscow",
    }
    sort_fields: tuple[str, ...]
    if name == WbRatingReportKind.COMPARISON:
        data = await service.comparison(typed_query)
        rows = [item.model_dump(mode="json") for item in data.rows]
        context.update(
            previous_from=data.previous_from.isoformat(),
            previous_to=data.previous_to.isoformat(),
            comparison_basis="last_saved_observation_within_each_period",
            latest_cache_is_separate=True,
            change_unit="rating_points",
            tolerance=query.tolerance,
        )
        totals = data.totals.model_dump(mode="json")
        sort_fields = (
            "article",
            "name",
            "latest_rating",
            "reviews_count",
            "current_rating",
            "previous_rating",
            "change",
            "status",
        )
        default_sort = "change"
    else:
        try:
            history = await service.history(typed_query)
        except LookupError as error:
            raise HTTPException(404, "Карточка WB не найдена в каталоге магазина") from error
        rows = [item.model_dump(mode="json") for item in history.rows]
        totals = {
            "expected_days": len(rows),
            "observed_days": history.observed_days,
            "missing_days": history.missing_days,
        }
        context.update(coverage_complete=history.missing_days == 0, history_basis="saved_daily_observations")
        sort_fields = ("day", "rating", "reviews_count", "observed_at")
        default_sort = "day"
    page_query = query.model_copy(
        update={
            "sort_by": query.sort_by or default_sort,
            "order": query.order if "order" in request.query_params else "asc",
            **({"offset": 0, "limit": max(1, len(rows))} if not paginate else {}),
        }
    )
    payload = reports.page(rows, page_query, sort_fields=sort_fields)
    payload.update(totals=totals, context=context)
    return reports.envelope(
        query,
        payload,
        (
            "Рейтинг склейки WB. Рейтинг отдельного товара без склейки может отличаться; этих данных в БД нет.",
            "Пропуски наблюдений не являются нулём. Свежий рейтинг не подставляется в прошлое. Число карточек не равно числу уникальных склеек.",
        ),
    )
