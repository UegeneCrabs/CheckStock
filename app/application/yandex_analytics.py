from collections.abc import Callable
from datetime import date, datetime, timedelta

from app.analytics import yandex_analyzer, yandex_ephemerides, yandex_sales_api
from app.analytics.yandex_sources import Sources, monday
from app.application.ports import YandexAnalyticsStore
from app.core.constants import YANDEX_ANALYTICS
from app.dto.identity import User
from app.dto.yandex_analytics import (
    AnalyzerResponse,
    Comment,
    CommentChange,
    CommentKey,
    DayNote,
    DSettings,
    EphemeridesResponse,
    FunnelDay,
    NoteChange,
    NoteCreate,
    SalesResponse,
    TransitSnapshot,
    WeeklyStock,
)


class AnalyticsConflict(ValueError):
    pass


class AnalyticsForbidden(ValueError):
    pass


class AnalyticsNotFound(ValueError):
    pass


class YandexAnalyticsService:
    def __init__(self, repository: YandexAnalyticsStore, clock: Callable[[], datetime]):
        self.repository = repository
        self.clock = clock

    def checked_week(self, value: date | None = None, *, exact: bool = False) -> date:
        today = self.clock().date()
        week = monday(value or today)
        if week < YANDEX_ANALYTICS.first_week or week > monday(today) or exact and week != value:
            raise ValueError("invalid_week")
        return week

    def sources(
        self, stores: tuple[str, ...], week: date, *, current: bool = True, history: bool = True
    ) -> Sources:
        today = self.clock().date()
        start = week - timedelta(days=YANDEX_ANALYTICS.week_days * 2)
        end = min(today, week + timedelta(days=YANDEX_ANALYTICS.week_days - 1))
        return self.repository.sources(stores, start, today, current=current, history=history, period_end=end)

    def analyzer(self, stores: tuple[str, ...], week: date | None) -> AnalyzerResponse:
        selected = self.checked_week(week)
        return yandex_analyzer.load(self.sources(stores, selected), selected, self.clock())

    def ephemerides(
        self, stores: tuple[str, ...], week: date | None, *, history: bool = False
    ) -> EphemeridesResponse:
        selected = self.checked_week(week)
        sources = self.sources(stores, selected, current=False, history=not history)
        return yandex_ephemerides.load(sources, selected, self.clock(), history=history)

    def sales(self, stores: tuple[str, ...], start: date | None, end: date | None) -> SalesResponse:
        today = self.clock().date()
        if (start is None) != (end is None):
            raise ValueError("period_pair_required")
        if start is not None and end is not None:
            if (
                start < YANDEX_ANALYTICS.first_day
                or end > today
                or start > end
                or (end - start).days >= YANDEX_ANALYTICS.max_days
            ):
                raise ValueError("invalid_period")
        sources = self.repository.sources(
            stores,
            start or YANDEX_ANALYTICS.first_day,
            today,
            current=False,
            history=False,
            historical_products=True,
            period_end=end,
        )
        return yandex_sales_api.load(sources, start, end)

    def require_product(self, stores: tuple[str, ...], store: str, article: str) -> None:
        if store not in stores or not self.repository.product_exists(store, article):
            raise AnalyticsNotFound("product_not_found")

    def add_note(self, stores: tuple[str, ...], change: NoteCreate, user: User) -> DayNote:
        self.require_product(stores, change.store, change.article)
        if change.day != self.clock().date():
            raise AnalyticsForbidden("note_today_only")
        with self.repository.unit_of_work() as work:
            note = work.add_note(change, user, self.clock())
            work.commit()
        return note.model_copy(update={"can_edit": True})

    def edit_note(self, stores: tuple[str, ...], note_id: int, change: NoteChange, user: User) -> DayNote:
        with self.repository.unit_of_work() as work:
            original = work.note(note_id)
            if original is None:
                raise AnalyticsNotFound("note_not_found")
            self.require_product(stores, original.store_slug, original.article)
            if original.action_date != self.clock().date().isoformat():
                raise AnalyticsForbidden("note_today_only")
            result = work.edit_note(original, change, user, self.clock())
            work.commit()
        return result.model_copy(update={"can_edit": True})

    def comments_key(self, stores: tuple[str, ...], key: CommentKey) -> None:
        self.checked_week(key.week, exact=True)
        self.require_product(stores, key.store, key.article)

    def save_comment(self, stores: tuple[str, ...], change: CommentChange, user: User) -> Comment:
        self.comments_key(stores, change)
        if change.kind == "decision" and change.week != monday(self.clock().date()):
            raise AnalyticsForbidden("decision_current_week_only")
        with self.repository.unit_of_work() as work:
            result = work.save_comment(change, user, self.clock())
            work.commit()
        return result

    def revisions(self, stores: tuple[str, ...], key: CommentKey) -> tuple[Comment, ...]:
        self.comments_key(stores, key)
        return self.repository.revisions(key)

    def settings(self, stores: tuple[str, ...], store: str) -> DSettings:
        if store not in stores:
            raise AnalyticsNotFound("store_not_found")
        return self.repository.settings(store)

    def save_settings(self, stores: tuple[str, ...], store: str, settings: DSettings) -> DSettings:
        if store not in stores:
            raise AnalyticsNotFound("store_not_found")
        with self.repository.unit_of_work() as work:
            result = work.save_settings(store, settings)
            work.commit()
        return result

    def save_funnel(self, snapshot: FunnelDay) -> None:
        if (
            snapshot.request is None
            or snapshot.request.date_from != snapshot.source_date
            or snapshot.request.date_to != snapshot.source_date
            or snapshot.source_date_origin != "single_day_request"
            or any(
                item.source_scope != "report_date" or item.source_date_origin is None
                for item in snapshot.products
            )
        ):
            raise ValueError("funnel_single_day_unverified")
        with self.repository.unit_of_work() as work:
            work.save_funnel(snapshot)
            work.commit()

    def funnel_days(self, store: str, refresh_seconds: int) -> tuple[date, ...]:
        return self.repository.funnel_days(store, self.clock(), refresh_seconds)

    def save_transit(self, snapshot: TransitSnapshot) -> None:
        with self.repository.unit_of_work() as work:
            work.save_transit(snapshot)
            work.commit()

    def source_error(self, store: str, source: str, error: str) -> None:
        with self.repository.unit_of_work() as work:
            work.source_error(store, source, error, self.clock())
            work.commit()

    def freeze_stock(self, snapshot: WeeklyStock) -> None:
        with self.repository.unit_of_work() as work:
            work.freeze_stock(snapshot)
            work.commit()
