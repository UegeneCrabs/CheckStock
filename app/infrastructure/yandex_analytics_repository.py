import json
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from types import TracebackType
from typing import Any

from app.analytics.yandex_sources import ProductSources, Sources, StoreSources, monday
from app.core.constants import YANDEX_ANALYTICS
from app.core.stores import STORES
from app.dto.identity import User
from app.dto.yandex_analytics import (
    ArticleBounds,
    CartSource,
    Comment,
    CommentChange,
    CommentKey,
    CurrentEconomics,
    DailyMetric,
    DayNote,
    DSettings,
    EconomicsDay,
    FunnelDay,
    NoteChange,
    NoteCreate,
    NoteRevision,
    Prices,
    Product,
    Reference,
    StoreBounds,
    TransitSnapshot,
    WeeklyStock,
)
from app.infrastructure.database import DatabaseConnection
from app.repositories import (
    catalog,
    core,
    daily_economics,
    yandex_economics,
    yandex_source_values,
    yandex_storefront,
)
from app.repositories import unit_economics_yandex as metrics
from app.yandex import economics, economics_shared
from app.yandex.economics_days import resolve_day

read_legacy_sources: Callable[..., Any] = yandex_economics.current_sources
read_legacy_context: Callable[..., Any] = economics.context
read_legacy_day_records: Callable[..., Any] = daily_economics.day_records
read_legacy_detail: Callable[..., Any] = economics.detail
read_legacy_metrics: Callable[..., Any] = economics.current_metrics
merge_legacy_history: Callable[..., Any] = economics_shared.history
resolve_legacy_day: Callable[..., Any] = resolve_day


def note_from_connection(connection: DatabaseConnection, note_id: int) -> DayNote | None:
    row = connection.execute(
        "SELECT * FROM rnp_action_log WHERE id=? AND marketplace=?", (note_id, YANDEX_ANALYTICS.marketplace)
    ).fetchone()
    if row is None:
        return None
    revisions = tuple(
        NoteRevision.model_validate(dict(item))
        for item in connection.execute(
            "SELECT * FROM rnp_action_revisions WHERE action_id=? ORDER BY edited_at,id", (note_id,)
        )
    )
    return DayNote.model_validate(
        {
            **dict(row),
            "revisions": revisions,
            "updated_at": revisions[-1].edited_at if revisions else None,
            "updated_by": revisions[-1].user_name if revisions else None,
        }
    )


def store_notes(connection: DatabaseConnection, store: str, start: date, end: date) -> tuple[DayNote, ...]:
    parameters = (store, YANDEX_ANALYTICS.marketplace, start.isoformat(), end.isoformat())
    notes = connection.execute(
        "SELECT * FROM rnp_action_log WHERE store_slug=? AND marketplace=? AND action_date>=? AND action_date<=? ORDER BY action_date,created_at,id",
        parameters,
    ).fetchall()
    revisions: dict[int, list[NoteRevision]] = {}
    for row in connection.execute(
        "SELECT r.* FROM rnp_action_revisions r JOIN rnp_action_log a ON a.id=r.action_id WHERE a.store_slug=? AND a.marketplace=? AND a.action_date>=? AND a.action_date<=? ORDER BY r.edited_at,r.id",
        parameters,
    ):
        revision = NoteRevision.model_validate(dict(row))
        revisions.setdefault(revision.action_id, []).append(revision)
    result: list[DayNote] = []
    for row in notes:
        history = tuple(revisions.get(int(str(row["id"])), ()))
        result.append(
            DayNote.model_validate(
                {
                    **dict(row),
                    "revisions": history,
                    "updated_at": history[-1].edited_at if history else None,
                    "updated_by": history[-1].user_name if history else None,
                }
            )
        )
    return tuple(result)


class YandexAnalyticsUnitOfWork:
    def __init__(self) -> None:
        self.connection: DatabaseConnection | None = None

    @property
    def db(self) -> DatabaseConnection:
        if self.connection is None:
            raise RuntimeError("inactive_unit_of_work")
        return self.connection

    def __enter__(self) -> "YandexAnalyticsUnitOfWork":
        self.connection = core.get_connection()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.db.rollback()
        self.db.close()
        self.connection = None

    def commit(self) -> None:
        self.db.commit()

    def note(self, note_id: int) -> DayNote | None:
        return note_from_connection(self.db, note_id)

    def add_note(self, change: NoteCreate, user: User, now: datetime) -> DayNote:
        row = self.db.execute(
            "INSERT INTO rnp_action_log (store_slug,marketplace,article,action_date,note,user_id,user_name,created_at) VALUES (?,?,?,?,?,?,?,?) RETURNING id",
            (
                change.store,
                YANDEX_ANALYTICS.marketplace,
                change.article,
                change.day.isoformat(),
                change.note,
                user.id,
                user.full_name,
                now.isoformat(),
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("note_insert_failed")
        note = self.note(int(str(row["id"])))
        if note is None:
            raise RuntimeError("note_insert_failed")
        return note

    def edit_note(self, original: DayNote, change: NoteChange, user: User, now: datetime) -> DayNote:
        from app.application.yandex_analytics import AnalyticsConflict

        changed = self.db.execute(
            "UPDATE rnp_action_log SET note=? WHERE id=? AND marketplace=? AND note=? AND action_date=?",
            (
                change.note,
                original.id,
                YANDEX_ANALYTICS.marketplace,
                change.previous_note,
                original.action_date,
            ),
        )
        if changed.rowcount != 1:
            raise AnalyticsConflict("note_conflict")
        self.db.execute(
            "INSERT INTO rnp_action_revisions (id,action_id,previous_note,note,user_id,user_name,edited_at) VALUES (?,?,?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                original.id,
                original.note,
                change.note,
                user.id,
                user.full_name,
                now.isoformat(),
            ),
        )
        result = self.note(original.id)
        if result is None:
            raise RuntimeError("note_update_failed")
        return result

    def save_comment(self, change: CommentChange, user: User, now: datetime) -> Comment:
        from app.application.yandex_analytics import AnalyticsConflict

        key = (change.store, change.article, change.week.isoformat(), change.kind)
        if change.version == 0:
            self.db.execute(
                "INSERT INTO yandex_analytics_comments (store_slug,article,week,kind,version,text,author_id,author,updated_at) VALUES (?,?,?,?,0,'',?,?,?) ON CONFLICT(store_slug,article,week,kind) DO NOTHING",
                (*key, user.id, user.full_name, now.isoformat()),
            )
        result = self.db.execute(
            "UPDATE yandex_analytics_comments SET version=version+1,text=?,author_id=?,author=?,updated_at=? WHERE store_slug=? AND article=? AND week=? AND kind=? AND version=?",
            (change.text, user.id, user.full_name, now.isoformat(), *key, change.version),
        )
        if result.rowcount != 1:
            raise AnalyticsConflict("comment_conflict")
        comment = Comment(
            **change.model_dump(exclude={"version"}),
            version=change.version + 1,
            author_id=user.id,
            author=user.full_name,
            updated_at=now.isoformat(),
        )
        self.db.execute(
            "INSERT INTO yandex_analytics_comment_revisions (store_slug,article,week,kind,version,text,author_id,author,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (*key, comment.version, comment.text, comment.author_id, comment.author, comment.updated_at),
        )
        return comment

    def save_settings(self, store: str, settings: DSettings) -> DSettings:
        from app.application.yandex_analytics import AnalyticsConflict

        if settings.revision == 0:
            self.db.execute(
                "INSERT INTO yandex_analytics_settings (store_slug,revision,payload_json) VALUES (?,0,?) ON CONFLICT(store_slug) DO NOTHING",
                (store, DSettings().model_dump_json()),
            )
        result = settings.model_copy(update={"revision": settings.revision + 1})
        changed = self.db.execute(
            "UPDATE yandex_analytics_settings SET revision=?,payload_json=? WHERE store_slug=? AND revision=?",
            (result.revision, result.model_dump_json(), store, settings.revision),
        )
        if changed.rowcount != 1:
            raise AnalyticsConflict("settings_conflict")
        return result

    def save_funnel(self, snapshot: FunnelDay) -> None:
        day = snapshot.source_date.isoformat()
        self.db.execute(
            "INSERT INTO yandex_analytics_funnel_days (store_slug,source_date,payload_json,received_at) VALUES (?,?,?,?) ON CONFLICT(store_slug,source_date) DO UPDATE SET payload_json=excluded.payload_json,received_at=excluded.received_at",
            (snapshot.store, day, snapshot.model_dump_json(), snapshot.received_at.isoformat()),
        )
        if any(item.day != day for item in snapshot.products) or len(
            {item.article for item in snapshot.products}
        ) != len(snapshot.products):
            raise ValueError("invalid_funnel_day")
        self.db.execute(
            "DELETE FROM unit_economics_yandex_daily_metrics WHERE store_slug=? AND source='funnel' AND day=?",
            (snapshot.store, day),
        )
        for item in snapshot.products:
            payload = item.model_copy(
                update={
                    "timezone": snapshot.timezone,
                    "received_at": snapshot.received_at,
                    "provisional": snapshot.provisional,
                }
            )
            self.db.execute(
                "INSERT INTO unit_economics_yandex_daily_metrics (store_slug,source,day,article,data_json,updated_at) VALUES (?,'funnel',?,?,?,?)",
                (
                    snapshot.store,
                    day,
                    item.article,
                    payload.model_dump_json(),
                    snapshot.received_at.isoformat(),
                ),
            )
        self.db.execute(
            "INSERT INTO unit_economics_yandex_loaded_days (store_slug,source,day,updated_at) VALUES (?,'funnel',?,?) ON CONFLICT(store_slug,source,day) DO UPDATE SET updated_at=excluded.updated_at",
            (snapshot.store, day, snapshot.received_at.isoformat()),
        )
        self.db.execute(
            "INSERT INTO unit_economics_yandex_snapshots (store_slug,source,last_success_at,last_attempt_at,error) VALUES (?,'funnel',?,?,NULL) ON CONFLICT(store_slug,source) DO UPDATE SET last_success_at=excluded.last_success_at,last_attempt_at=excluded.last_attempt_at,error=NULL",
            (snapshot.store, snapshot.received_at.isoformat(), snapshot.received_at.isoformat()),
        )

    def save_transit(self, snapshot: TransitSnapshot) -> None:
        self.db.execute(
            "INSERT INTO yandex_analytics_customer_transit (store_slug,payload_json,last_success_at,last_attempt_at,error) VALUES (?,?,?,?,NULL) ON CONFLICT(store_slug) DO UPDATE SET payload_json=excluded.payload_json,last_success_at=excluded.last_success_at,last_attempt_at=excluded.last_attempt_at,error=NULL",
            (
                snapshot.store,
                snapshot.model_dump_json(),
                snapshot.received_at.isoformat(),
                snapshot.received_at.isoformat(),
            ),
        )

    def source_error(self, store: str, source: str, error: str, now: datetime) -> None:
        if source == "transit":
            self.db.execute(
                "INSERT INTO yandex_analytics_customer_transit (store_slug,last_attempt_at,error) VALUES (?,?,?) ON CONFLICT(store_slug) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,error=excluded.error",
                (store, now.isoformat(), error),
            )
        else:
            self.db.execute(
                "INSERT INTO unit_economics_yandex_snapshots (store_slug,source,last_attempt_at,error) VALUES (?,?,?,?) ON CONFLICT(store_slug,source) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,error=excluded.error",
                (store, source, now.isoformat(), error),
            )

    def freeze_stock(self, snapshot: WeeklyStock) -> None:
        self.db.execute(
            "INSERT INTO yandex_analytics_stock_snapshots (store_slug,week,payload_json,captured_at) VALUES (?,?,?,?) ON CONFLICT(store_slug,week) DO NOTHING",
            (
                snapshot.store,
                snapshot.week.isoformat(),
                snapshot.model_dump_json(),
                snapshot.captured_at.isoformat(),
            ),
        )


class YandexAnalyticsRepository:
    def unit_of_work(self) -> YandexAnalyticsUnitOfWork:
        return YandexAnalyticsUnitOfWork()

    def funnel_days(self, store: str, now: datetime, refresh_seconds: int) -> tuple[date, ...]:
        today = now.date()
        first = today - timedelta(days=YANDEX_ANALYTICS.funnel_recent_days - 1)
        with core.get_connection() as connection:
            records = connection.execute(
                "SELECT source_date,received_at FROM yandex_analytics_funnel_days WHERE store_slug=? AND source_date>=? AND source_date<=?",
                (store, first.isoformat(), today.isoformat()),
            )
            received = {
                str(row["source_date"]): datetime.fromisoformat(str(row["received_at"])) for row in records
            }
        return tuple(
            today - timedelta(days=offset)
            for offset in range(YANDEX_ANALYTICS.funnel_recent_days)
            if offset == 0
            or (day := (today - timedelta(days=offset)).isoformat()) not in received
            or (now - received[day]).total_seconds() >= refresh_seconds
        )

    def product_exists(self, store: str, article: str) -> bool:
        with core.get_connection() as connection:
            return (
                connection.execute(
                    "SELECT article FROM stock_items WHERE store_slug=? AND marketplace=? AND article=?",
                    (store, YANDEX_ANALYTICS.marketplace, article),
                ).fetchone()
                is not None
            )

    def settings(self, store: str) -> DSettings:
        with core.get_connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM yandex_analytics_settings WHERE store_slug=?", (store,)
            ).fetchone()
        return DSettings.model_validate_json(str(row["payload_json"])) if row else DSettings()

    def revisions(self, key: CommentKey) -> tuple[Comment, ...]:
        with core.get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM yandex_analytics_comment_revisions WHERE store_slug=? AND article=? AND week=? AND kind=? ORDER BY version DESC",
                (key.store, key.article, key.week.isoformat(), key.kind),
            )
            return tuple(Comment.model_validate({**dict(row), "store": row["store_slug"]}) for row in rows)

    def transit(self, store: str) -> TransitSnapshot | None:
        with core.get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM yandex_analytics_customer_transit WHERE store_slug=?", (store,)
            ).fetchone()
        if not row or not row["payload_json"]:
            return None
        return TransitSnapshot.model_validate_json(str(row["payload_json"])).model_copy(
            update={"error": row["error"]}
        )

    def _bounds(self, store: str, today: date) -> StoreBounds:
        with core.get_connection() as connection:
            loaded = connection.execute(
                "SELECT MIN(day) AS first_day,MAX(day) AS last_day FROM unit_economics_yandex_loaded_days WHERE store_slug=? AND source='orders' AND day<=?",
                (store, today.isoformat()),
            ).fetchone()
            articles = tuple(
                ArticleBounds.model_validate(dict(row))
                for row in connection.execute(
                    "SELECT article,MIN(day) AS first_day,MAX(day) AS last_day FROM unit_economics_yandex_daily_metrics WHERE store_slug=? AND source='orders' AND day<=? GROUP BY article",
                    (store, today.isoformat()),
                )
            )
        firsts = [item.first_day for item in articles]
        lasts = [item.last_day for item in articles]
        if loaded and loaded["first_day"]:
            firsts.append(date.fromisoformat(str(loaded["first_day"])))
            lasts.append(date.fromisoformat(str(loaded["last_day"])))
        return StoreBounds(
            first_day=min(firsts, default=None), last_day=max(lasts, default=None), articles=articles
        )

    def sources(
        self,
        stores: tuple[str, ...],
        start: date,
        today: date,
        *,
        current: bool = True,
        history: bool = True,
        historical_products: bool = False,
        period_end: date | None = None,
    ) -> Sources:
        result: list[StoreSources] = []
        saved_bounds = {store: self._bounds(store, today) for store in stores} if historical_products else {}
        if historical_products and period_end is None:
            last = max((item.last_day for item in saved_bounds.values() if item.last_day), default=today)
            period_end = min(today, last)
            start = period_end - timedelta(days=YANDEX_ANALYTICS.week_days - 1)
        end = period_end or today
        windows = (
            ((start, end),)
            if end == today
            else ((start, end), (monday(today) - timedelta(days=YANDEX_ANALYTICS.week_days), today))
        )
        for store in stores:
            state = StoreSources(slug=store, coefficients=self.settings(store))
            references = yandex_source_values.get_values(store)
            prices = yandex_storefront.get_prices(store)
            saved_sources = read_legacy_sources(store)
            for raw in catalog.get_stock_items(store, YANDEX_ANALYTICS.marketplace, ("fbs", "fbo")):
                product = Product.model_validate(
                    {
                        **raw,
                        "store_slug": store,
                        "ff_available": raw.get("ff_available") if raw.get("ff_available") is not None else 0,
                        "name": raw.get("name") or str(raw["article"]),
                        "barcode": raw.get("barcode") or "",
                        "image_url": raw.get("image_url") or "",
                    }
                )
                category = saved_sources.get((product.article, "category"), {}).get("values", {})
                catalog_source = saved_sources.get((product.article, "catalog"), {}).get("values", {})
                resolved_prices = yandex_storefront.resolved_prices(prices.get(product.article, {}))
                state.products[product.article] = ProductSources(
                    product=product,
                    project=str(STORES[store]["name"]),
                    reference=Reference.model_validate(references.get(product.article, {})),
                    category=str(category.get("category_name") or catalog_source.get("category_name") or ""),
                    prices=Prices.model_validate(
                        {
                            **resolved_prices,
                            "updated_at": prices.get(product.article, {}).get("checked_at"),
                            "stale": not yandex_storefront.fresh(
                                prices.get(product.article, {}).get("checked_at")
                            ),
                        }
                    ),
                )
            with core.get_connection() as connection:
                if historical_products:
                    bounds = saved_bounds[store]
                    state.first_day = bounds.first_day.isoformat() if bounds.first_day else None
                    state.last_day = bounds.last_day.isoformat() if bounds.last_day else None
                    for item in bounds.articles:
                        if item.article not in state.products:
                            state.products[item.article] = ProductSources(
                                product=Product(store_slug=store, article=item.article),
                                project=str(STORES[store]["name"]),
                            )
                for note in store_notes(connection, store, start, today) if history else ():
                    if note.article in state.products:
                        note_source = state.products[note.article]
                        note_source.notes = (*note_source.notes, note)
                state.comments = tuple(
                    Comment.model_validate({**dict(row), "store": store})
                    for row in connection.execute(
                        "SELECT * FROM yandex_analytics_comments WHERE store_slug=? AND week>=? AND week<=?",
                        (
                            store,
                            (start - timedelta(weeks=YANDEX_ANALYTICS.history_weeks)).isoformat(),
                            end.isoformat(),
                        ),
                    )
                )
                state.stock = (
                    {
                        str(row["week"]): WeeklyStock.model_validate_json(str(row["payload_json"]))
                        for row in connection.execute(
                            "SELECT week,payload_json FROM yandex_analytics_stock_snapshots WHERE store_slug=? AND week>=? AND week<=?",
                            (store, start.isoformat(), end.isoformat()),
                        )
                    }
                    if history
                    else {}
                )
                for first, last in windows:
                    for record in connection.execute(
                        "SELECT source_date,payload_json FROM yandex_analytics_funnel_days WHERE store_slug=? AND source_date>=? AND source_date<=?",
                        (store, first.isoformat(), last.isoformat()),
                    ):
                        funnel_day = FunnelDay.model_validate_json(str(record["payload_json"]))
                        state.funnel_metadata[str(record["source_date"])] = CartSource(
                            source_date=funnel_day.source_date,
                            received_at=funnel_day.received_at,
                            source_date_origin=funnel_day.source_date_origin,
                        )
            for source_name, field, loaded_field in (
                ("orders", "orders", "orders_loaded"),
                ("advertising", "ads", "ads_loaded"),
                ("funnel", "funnel", "funnel_loaded"),
            ):
                raw_metrics = []
                loaded: set[str] = set()
                for first, last in windows:
                    window_rows, window_loaded = metrics.get_history(
                        store, source_name, first.isoformat(), last.isoformat()
                    )
                    raw_metrics.extend(window_rows)
                    loaded.update(window_loaded)
                setattr(state, loaded_field, loaded)
                for raw_metric in raw_metrics:
                    metric = DailyMetric.model_validate(raw_metric)
                    metric_source = state.products.get(metric.article)
                    if metric_source:
                        getattr(metric_source, field)[metric.day] = metric
                        if not metric_source.product.name and raw_metric.get("product_name"):
                            metric_source.product = metric_source.product.model_copy(
                                update={"name": str(raw_metric["product_name"])}
                            )
            times = metrics.loaded_day_times(store, "orders", start.isoformat(), today.isoformat())
            state.orders_updated_at = max(times.values(), default=None)
            snapshots = metrics.get_snapshots(store)
            reputation = snapshots.get("reputation", {}).get("data") or []
            state.funnel_error = snapshots.get("funnel", {}).get("error")
            state.orders_error = snapshots.get("orders", {}).get("error")
            state.ads_error = snapshots.get("advertising", {}).get("error")
            for raw in reputation:
                article = str(raw.get("sku") or raw.get("article") or "")
                if article in state.products:
                    state.products[article].rating = (
                        float(raw["rating"]) if raw.get("rating") is not None else None
                    )
            if history:
                self._economy(state, start, end)
            if current:
                cache = read_legacy_context(store, include_day_inputs=False)
                key = today.isoformat()
                cache["day_records"] = read_legacy_day_records(YANDEX_ANALYTICS.marketplace, store, key)
                cache["day_records_day"] = key
                for article, source in state.products.items():
                    raw = read_legacy_detail(
                        store,
                        article,
                        economics_shared.SCHEME,
                        today=today,
                        state_cache=cache,
                        daily_metrics=read_legacy_metrics(
                            store,
                            article,
                            today,
                            history=(
                                [item.model_dump() for item in source.ads.values() if item.day == key],
                                state.ads_loaded,
                                [item.model_dump() for item in source.orders.values() if item.day == key],
                                state.orders_loaded,
                            ),
                        ),
                        include_history=False,
                    )
                    source.current = CurrentEconomics(
                        roi=raw["result"].get("roi"),
                        profit=raw["result"].get("margin"),
                        purchase=raw["values"].get("purchase_price"),
                        complete=not raw["current_issues"]["roi"],
                    )
            result.append(state)
        return Sources(stores=tuple(result), today=today)

    def _economy(self, state: StoreSources, start: date, today: date) -> None:
        saved_index: dict[tuple[str, str, str], dict[str, Any]] = {}
        dated: dict[tuple[str, str], EconomicsDay] = {}
        with core.get_connection() as connection:
            with connection.stream(
                "SELECT article,scheme,day,payload_json FROM yandex_economics_daily WHERE store_slug=? AND day>=? AND day<=? ORDER BY day",
                (state.slug, start.isoformat(), today.isoformat()),
            ) as records:
                for row in records:
                    article, scheme, day = str(row["article"]), str(row["scheme"]), str(row["day"])
                    saved_index[(article, scheme, day)] = {
                        "article": article,
                        "scheme": scheme,
                        "day": day,
                        "data": json.loads(str(row["payload_json"])),
                    }
            with connection.stream(
                "SELECT article,day,payload_json FROM economics_daily WHERE marketplace=? AND store_slug=? AND day>=? AND day<=? ORDER BY day",
                (YANDEX_ANALYTICS.marketplace, state.slug, start.isoformat(), today.isoformat()),
            ) as records:
                for row in records:
                    article, day = str(row["article"]), str(row["day"])
                    payload = json.loads(str(row["payload_json"]))
                    observation = payload["source"]
                    overrides = payload.get("overrides") or {}
                    values = {**observation["values"], **overrides}
                    saved_index[(article, economics_shared.SCHEME, day)] = {
                        "article": article,
                        "scheme": economics_shared.SCHEME,
                        "day": day,
                        "data": {
                            "day": day,
                            "inputs": values,
                            "overrides": overrides,
                            "calculation_version": observation.get("version"),
                        },
                    }
                    raw = observation.get("raw") or {}
                    reference, prices = raw.get("source_1c") or {}, raw.get("prices") or {}
                    dated[(article, day)] = EconomicsDay(
                        day=day,
                        goal=reference.get("goal_week"),
                        goal_known="goal_week" in reference,
                        prices=Prices(
                            seller_price=prices.get("seller_price"),
                            buyer_price=prices.get("buyer_price"),
                            pay_price=prices.get("pay_price"),
                            updated_at=day,
                        ),
                    )
            with connection.stream(
                "SELECT article,source,payload_json FROM yandex_economics_sources WHERE store_slug=? AND source>=? AND source<=?",
                (
                    state.slug,
                    "day-input:" + start.isoformat(),
                    "day-input:" + today.isoformat() + ":" + economics_shared.SCHEME,
                ),
            ) as records:
                for row in records:
                    name = str(row["source"])
                    if not name.endswith(":" + economics_shared.SCHEME):
                        continue
                    article = str(row["article"])
                    day = name.removeprefix("day-input:").removesuffix(":" + economics_shared.SCHEME)
                    key = (article, economics_shared.SCHEME, day)
                    if key in saved_index:
                        continue
                    payload = json.loads(str(row["payload_json"]))
                    if payload.get("basis") not in {"today_prices", "today_observations"}:
                        continue
                    saved_index[key] = {
                        "article": article,
                        "day": day,
                        "scheme": economics_shared.SCHEME,
                        "data": {
                            "day": day,
                            "inputs": payload.get("values") or {},
                            "calculation_version": payload.get("version"),
                        },
                    }
        saved_rows = list(saved_index.values())
        for article, source in state.products.items():
            for saved in merge_legacy_history(saved_rows, article):
                day = str(saved["day"])
                resolved = resolve_legacy_day(
                    day,
                    saved,
                    source.orders[day].model_dump() if day in source.orders else {},
                    source.ads[day].model_dump() if day in source.ads else {},
                    day in state.orders_loaded,
                    day in state.ads_loaded,
                )
                inputs = resolved["inputs"]
                source.economy[day] = EconomicsDay(
                    day=day,
                    buyout=inputs.get("buyout_percent"),
                    profit=resolved.get("profit"),
                    purchase=resolved.get("purchase_value"),
                    complete=resolved.get("complete", False),
                    messages=tuple(resolved.get("messages") or ()),
                    prices=Prices(
                        seller_price=inputs.get("seller_price"),
                        buyer_price=inputs.get("buyer_price"),
                        pay_price=inputs.get("pay_price"),
                        updated_at=day,
                    ),
                )
                saved_source = dated.get((article, day))
                if saved_source is not None:
                    source.economy[day] = source.economy[day].model_copy(
                        update={
                            "goal": saved_source.goal,
                            "goal_known": saved_source.goal_known,
                            "prices": saved_source.prices,
                        }
                    )
