from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from datetime import date, datetime
from types import TracebackType
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from app.core.constants import OzonAnalyticsReason
from app.dto.database_backup import BackupArtifact, BackupReceipt, BackupUpload, DriveBackupFile
from app.dto.fbo_stock import (
    FboActor,
    FboEvidence,
    FboMovement,
    FboStockCommand,
    FboStockReport,
    FboStockResult,
    FboTarget,
)
from app.dto.identity import User
from app.dto.orders import OzonArticleIndex
from app.dto.ozon_analytics import (
    OzonAnalyticsAuthor,
    OzonAnalyticsCatalog,
    OzonAnalyticsComment,
    OzonAnalyticsCommentChange,
    OzonAnalyticsCommentKey,
    OzonAnalyticsCommentRevision,
    OzonAnalyticsForecastSettings,
    OzonAnalyticsFunnelPage,
    OzonAnalyticsFunnelRun,
    OzonAnalyticsNote,
    OzonAnalyticsNoteCreate,
    OzonAnalyticsNoteRevision,
    OzonAnalyticsNoteUpdate,
    OzonAnalyticsReference,
    OzonAnalyticsReservation,
    OzonAnalyticsSalesSources,
    OzonAnalyticsSourceBundle,
    OzonAnalyticsSourceStatus,
    OzonAnalyticsStock,
    OzonAnalyticsStockSnapshot,
    OzonAnalyticsTransit,
)
from app.dto.ozon_calendar import OzonCalendarEvent, OzonCalendarKey, OzonCalendarSave, OzonCalendarSources
from app.dto.ozon_limits import (
    OzonRateFeedback,
    OzonRateScope,
    PerformanceAdmission,
    PerformanceCompletion,
    PerformanceIdentity,
    PerformanceLease,
    PerformanceQuotaRequest,
)
from app.dto.ozon_reports import (
    OzonProfitQuery,
    OzonReportCatalogItem,
    OzonReportEvidence,
    OzonTargetRecord,
    OzonTargetSave,
)
from app.dto.yandex_analytics import (
    Comment,
    CommentChange,
    CommentKey,
    DayNote,
    DSettings,
    FunnelDay,
    NoteChange,
    NoteCreate,
    TransitSnapshot,
    WeeklyStock,
)

if TYPE_CHECKING:
    from app.analytics.yandex_sources import Sources
    from app.infrastructure.database import DatabaseConnection


from app.dto.identity import (
    AccessDecision,
    ActivityCommand,
    ActivityLog,
    ActivityLogQuery,
    CountResult,
    CreateSessionCommand,
    CreateUserCommand,
    ExpiredSessionsCommand,
    LoginQuery,
    PasswordHash,
    PasswordHashRequest,
    PasswordVerification,
    SessionData,
    SessionToken,
    UserActiveChange,
    UserCollection,
    UserCountQuery,
    UserId,
    UserPasswordChange,
    WbTokenInfoCollection,
    WbTokenInfoCommand,
)
from app.dto.ozon_cabinet import OzonBuyoutSnapshot, OzonCabinetRecord, OzonCabinetSave
from app.dto.ozon_history import OzonHistoryEvidence, OzonHistoryQuery
from app.dto.ozon_logistics import LogisticsStoreSnapshot
from app.dto.ozon_margin import OzonMarginSnapshot
from app.dto.ozon_price_changes import (
    OzonPriceChangeOperation,
    OzonPriceChangePayload,
    OzonPriceChangeReceipt,
)
from app.dto.ozon_prices import SellerPriceItem
from app.dto.ozon_promotion_entry import OzonEntryIdentity, OzonEntryOperation
from app.dto.ozon_promotions import OzonBoostRecord, OzonBoostSave
from app.dto.ozon_storage import StorageEvidence, StoragePeriod, StorageReportTicket, StorageStoreSnapshot
from app.dto.spp_history import DailySppRow, HourlySppRow, SppHistoryQuery
from app.dto.stock import (
    ApplyShipmentCommand,
    ApplyTransferCommand,
    CancelTransitCommand,
    CatalogItems,
    CatalogQuery,
    ReceiveTransitCommand,
    ReopenTransitCommand,
    StockIncrement,
    StockQuantity,
    StockQuantityQuery,
    TransitActionResult,
)
from app.dto.supply_arrivals import SupplyStockCommand, SupplyStockState
from app.dto.wb_hourly_prices import WbHourlyPriceObservation, WbHourlyTurnover
from app.dto.wb_product_rating import WbRatingObservation
from app.dto.wb_standard_costs import PublishSnapshot

if TYPE_CHECKING:
    from app.infrastructure.database import DatabaseConnection


class WbRatingWriter(Protocol):
    def upsert(
        self,
        observations: tuple[WbRatingObservation, ...],
        valid_ratings: tuple[WbRatingObservation, ...],
    ) -> int: ...


class FboStockUnitOfWork(Protocol):
    async def evidence(self) -> FboEvidence: ...

    async def claim(self, command: FboStockCommand) -> FboStockResult | None: ...

    async def finish(self, command: FboStockCommand, result: FboStockResult) -> None: ...

    async def save(
        self,
        previous: FboMovement | None,
        movement: FboMovement,
        actor: FboActor,
        *,
        adopt: tuple[int, ...] = (),
    ) -> FboMovement: ...


class FboStockRepository(Protocol):
    def transaction(self, target: FboTarget) -> AbstractAsyncContextManager[FboStockUnitOfWork]: ...

    async def report(self, target: FboTarget) -> FboStockReport: ...


class WbStandardCostsRepository(Protocol):
    def save(self, command: PublishSnapshot) -> None: ...


class WbStandardCostsUnitOfWork(Protocol):
    repository: WbStandardCostsRepository

    def __enter__(self) -> WbStandardCostsUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class WbStandardCostsUnitOfWorkFactory(Protocol):
    def __call__(self) -> WbStandardCostsUnitOfWork: ...


class SupplyStockUnitOfWork(Protocol):
    async def states(self, source_key: str) -> tuple[SupplyStockState, ...]: ...

    async def save(self, command: SupplyStockCommand) -> SupplyStockState: ...


class OzonHistoryReader(Protocol):
    async def load(self, query: OzonHistoryQuery, start: date, end: date) -> OzonHistoryEvidence: ...


class OzonMarginStore(Protocol):
    async def current(self, stores: tuple[str, ...], now: datetime) -> tuple[OzonMarginSnapshot, ...]: ...

    async def save(self, snapshots: tuple[OzonMarginSnapshot, ...], now: datetime) -> None: ...


class OzonCabinetUnitOfWork(Protocol):
    async def boost_settings(self) -> OzonBoostRecord: ...

    async def save_boost(self, command: OzonBoostSave) -> OzonBoostRecord: ...

    async def settings(self, stores: tuple[str, ...]) -> tuple[OzonCabinetRecord, ...]: ...

    async def save(self, command: OzonCabinetSave) -> OzonCabinetRecord: ...

    async def save_buyout(self, snapshot: OzonBuyoutSnapshot) -> None: ...

    async def fail_buyout(self, store: str, error: str) -> None: ...


class OzonPriceChangeStore(Protocol):
    async def create(self, operation: OzonPriceChangeOperation) -> None: ...

    async def get(self, operation_id: UUID) -> OzonPriceChangeOperation: ...

    async def history(
        self, store: str, article: str, user_id: int
    ) -> tuple[OzonPriceChangeOperation, ...]: ...

    async def save(self, operation: OzonPriceChangeOperation, *, release: bool = False) -> None: ...

    async def claim(self, operation: OzonPriceChangeOperation) -> bool: ...

    async def attempts(self, store: str, article: str, since: datetime) -> int: ...


class OzonPriceChangeClient(Protocol):
    async def identity(self, article: str) -> OzonEntryIdentity: ...

    async def price(self, article: str, product_id: int) -> SellerPriceItem: ...

    async def update(self, payload: OzonPriceChangePayload) -> OzonPriceChangeReceipt: ...


class OzonPromotionEntryStore(Protocol):
    async def create(self, operation: OzonEntryOperation) -> None: ...

    async def get(self, operation_id: UUID) -> OzonEntryOperation: ...

    async def history(self, store: str, article: str, user_id: int) -> tuple[OzonEntryOperation, ...]: ...

    async def save(self, operation: OzonEntryOperation, *, release: bool = False) -> None: ...

    async def claim(self, operation: OzonEntryOperation) -> bool: ...


class StorageSource(Protocol):
    async def create(self, period: StoragePeriod) -> str: ...

    async def evidence(self, period: StoragePeriod, code: str) -> StorageEvidence: ...


class StorageUnitOfWork(Protocol):
    async def completed(self, store: str, day: date) -> bool: ...

    async def articles(self, store: str) -> tuple[str, ...]: ...

    async def ticket(self, store: str, day: date) -> StorageReportTicket: ...

    async def save_ticket(self, store: str, day: date, ticket: StorageReportTicket) -> None: ...

    async def commit(self, snapshot: StorageStoreSnapshot) -> None: ...

    async def fail(self, store: str, day: date, error: str) -> None: ...


class LogisticsUnitOfWork(Protocol):
    async def completed(self, store: str, day: date) -> bool: ...

    async def articles(self, store: str) -> tuple[str, ...]: ...

    async def commit(self, snapshot: LogisticsStoreSnapshot) -> None: ...

    async def fail(self, store: str, day: date, error: str) -> None: ...


class HourlyPriceWriter(Protocol):
    def upsert(self, rows: list[WbHourlyPriceObservation]) -> int: ...

    def daily_turnover(self, store_slug: str, day: date) -> tuple[WbHourlyTurnover, ...]: ...


class IdentityRepository(Protocol):
    def create_user(self, command: CreateUserCommand) -> UserId: ...

    def get_user(self, query: UserId) -> User | None: ...

    def get_user_by_login(self, query: LoginQuery) -> User | None: ...

    def list_users(self) -> UserCollection: ...

    def count_users(self) -> CountResult: ...

    def count_active_users(self, query: UserCountQuery) -> CountResult: ...

    def set_active(self, command: UserActiveChange) -> None: ...

    def update_password(self, command: UserPasswordChange) -> None: ...

    def delete_user(self, command: UserId) -> None: ...

    def create_session(self, command: CreateSessionCommand) -> None: ...

    def get_session(self, query: SessionToken) -> SessionData | None: ...

    def delete_session(self, command: SessionToken) -> None: ...

    def delete_sessions_for_user(self, command: UserId) -> None: ...

    def delete_expired_sessions(self, command: ExpiredSessionsCommand) -> None: ...

    def add_activity(self, command: ActivityCommand) -> None: ...

    def get_activity(self, query: ActivityLogQuery) -> ActivityLog: ...

    def upsert_wb_token_info(self, command: WbTokenInfoCommand) -> None: ...

    def get_wb_token_infos(self) -> WbTokenInfoCollection: ...


class IdentityUnitOfWork(Protocol):
    identities: IdentityRepository

    def __enter__(self) -> IdentityUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class IdentityUnitOfWorkFactory(Protocol):
    def __call__(self) -> IdentityUnitOfWork: ...


class PasswordService(Protocol):
    def hash(self, request: PasswordHashRequest) -> PasswordHash: ...

    def verify(self, request: PasswordVerification) -> AccessDecision: ...


class StockRepository(Protocol):
    def catalog(self, query: CatalogQuery) -> CatalogItems: ...

    def quantity(self, query: StockQuantityQuery) -> StockQuantity: ...

    def increment(self, command: StockIncrement) -> None: ...

    def apply_transfer(self, command: ApplyTransferCommand) -> int: ...

    def receive_transfer(self, command: ReceiveTransitCommand) -> TransitActionResult: ...

    def reopen_transfer(self, command: ReopenTransitCommand) -> TransitActionResult: ...

    def cancel_transfer(self, command: CancelTransitCommand) -> TransitActionResult: ...

    def apply_shipment(self, command: ApplyShipmentCommand) -> None: ...


class StockUnitOfWork(Protocol):
    repository: StockRepository

    @property
    def connection(self) -> DatabaseConnection: ...

    def __enter__(self) -> StockUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class StockUnitOfWorkFactory(Protocol):
    def __call__(self) -> StockUnitOfWork: ...


class SppHistoryRepository(Protocol):
    def read(self, query: SppHistoryQuery) -> list[HourlySppRow] | list[DailySppRow]: ...


class OzonLimitStore(Protocol):
    async def reserve_rate(self, scopes: tuple[OzonRateScope, ...], now: datetime) -> float: ...

    async def observe_rate(self, scope: OzonRateScope, feedback: OzonRateFeedback, now: datetime) -> None: ...

    async def reserve_performance(
        self, request: PerformanceQuotaRequest, now: datetime
    ) -> PerformanceAdmission: ...

    async def finish_performance(self, completion: PerformanceCompletion) -> None: ...

    async def acquire_report(
        self, identity: PerformanceIdentity, now: datetime
    ) -> PerformanceLease | None: ...

    async def renew_report(self, lease: PerformanceLease, now: datetime) -> None: ...

    async def release_report(self, lease: PerformanceLease) -> None: ...

    async def resolve_report(self, identity: PerformanceIdentity, request_key: str) -> None: ...


class OzonCalendarStore(Protocol):
    async def days(self, store: str, day: date, article: str = "") -> OzonCalendarSources: ...
    async def events(self, key: OzonCalendarKey) -> tuple[OzonCalendarEvent, ...]: ...
    async def save(self, command: OzonCalendarSave) -> None: ...


class OzonReportsStore(Protocol):
    async def catalog(self, stores: tuple[str, ...]) -> tuple[OzonReportCatalogItem, ...]: ...
    async def has_product(self, store: str, article: str) -> bool: ...
    async def evidence(self, query: OzonProfitQuery, stores: tuple[str, ...]) -> OzonReportEvidence: ...
    async def targets(self, stores: tuple[str, ...]) -> dict[tuple[str, str], OzonTargetRecord]: ...
    async def save_target(self, command: OzonTargetSave) -> OzonTargetRecord: ...


class BackupStore(Protocol):
    async def receipt(self, day: date) -> BackupReceipt | None: ...
    async def prepare(self, day: date) -> BackupUpload: ...
    async def save_upload(self, upload: BackupUpload) -> None: ...
    async def complete(self, artifact: BackupArtifact, file_id: str) -> None: ...
    async def cleanup(self, artifact: BackupArtifact) -> None: ...


class BackupDestination(Protocol):
    async def check_folder(self) -> None: ...
    async def upload(
        self, upload: BackupUpload, save: Callable[[BackupUpload], Awaitable[None]]
    ) -> DriveBackupFile: ...


class YandexAnalyticsUnitOfWork(Protocol):
    def __enter__(self) -> YandexAnalyticsUnitOfWork: ...
    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None: ...
    def commit(self) -> None: ...
    def note(self, note_id: int) -> DayNote | None: ...
    def add_note(self, change: NoteCreate, user: User, now: datetime) -> DayNote: ...
    def edit_note(self, original: DayNote, change: NoteChange, user: User, now: datetime) -> DayNote: ...
    def save_comment(self, change: CommentChange, user: User, now: datetime) -> Comment: ...
    def save_settings(self, store: str, settings: DSettings) -> DSettings: ...
    def save_funnel(self, snapshot: FunnelDay) -> None: ...
    def save_transit(self, snapshot: TransitSnapshot) -> None: ...
    def source_error(self, store: str, source: str, error: str, now: datetime) -> None: ...
    def freeze_stock(self, snapshot: WeeklyStock) -> None: ...


class YandexAnalyticsStore(Protocol):
    def funnel_days(self, store: str, now: datetime, refresh_seconds: int) -> tuple[date, ...]: ...
    def unit_of_work(self) -> AbstractContextManager[YandexAnalyticsUnitOfWork]: ...
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
    ) -> Sources: ...
    def product_exists(self, store: str, article: str) -> bool: ...
    def settings(self, store: str) -> DSettings: ...
    def revisions(self, key: CommentKey) -> tuple[Comment, ...]: ...


class OzonAnalyticsClient(Protocol):
    async def funnel(
        self, source_day: date, offset: int, catalog: OzonArticleIndex
    ) -> OzonAnalyticsFunnelPage: ...
    async def transit(self, store: str, now: datetime) -> OzonAnalyticsTransit: ...


class OzonAnalyticsStore(Protocol):
    async def forecast_settings(self, store: str) -> OzonAnalyticsForecastSettings: ...
    async def save_forecast(
        self, store: str, change: OzonAnalyticsForecastSettings, author: OzonAnalyticsAuthor, now: datetime
    ) -> OzonAnalyticsForecastSettings: ...
    async def catalog(self, stores: tuple[str, ...]) -> tuple[OzonAnalyticsCatalog, ...]: ...
    async def product_exists(self, store: str, article: str) -> bool: ...
    async def load(
        self, stores: tuple[str, ...], start: date, end: date, now: datetime, *, include_notes: bool = True
    ) -> OzonAnalyticsSourceBundle: ...
    async def sales_sources(
        self, stores: tuple[str, ...], start: date, end: date, today: date
    ) -> OzonAnalyticsSalesSources: ...
    async def notes(
        self, stores: tuple[str, ...], start: date, end: date, today: date
    ) -> tuple[OzonAnalyticsNote, ...]: ...
    async def note(self, note_id: int) -> OzonAnalyticsNote | None: ...
    async def create_note(
        self, payload: OzonAnalyticsNoteCreate, author: OzonAnalyticsAuthor, now: datetime
    ) -> OzonAnalyticsNote: ...
    async def update_note(
        self,
        original: OzonAnalyticsNote,
        change: OzonAnalyticsNoteUpdate,
        author: OzonAnalyticsAuthor,
        now: datetime,
    ) -> OzonAnalyticsNote: ...
    async def note_revisions(self, note_id: int) -> tuple[OzonAnalyticsNoteRevision, ...]: ...
    async def comments(
        self, stores: tuple[str, ...], start: date, end: date
    ) -> tuple[OzonAnalyticsComment, ...]: ...
    async def save_comment(
        self, change: OzonAnalyticsCommentChange, author: OzonAnalyticsAuthor, now: datetime
    ) -> OzonAnalyticsComment: ...
    async def comment_revisions(
        self, key: OzonAnalyticsCommentKey
    ) -> tuple[OzonAnalyticsCommentRevision, ...]: ...
    async def save_references(self, values: tuple[OzonAnalyticsReference, ...], now: datetime) -> None: ...
    async def save_stock(self, snapshot: OzonAnalyticsStockSnapshot) -> None: ...
    async def capture_stocks(self, store: str, now: datetime) -> tuple[OzonAnalyticsStock, ...]: ...
    async def save_transit(self, snapshot: OzonAnalyticsTransit) -> None: ...
    async def save_status(self, status: OzonAnalyticsSourceStatus) -> None: ...
    async def reserve_attempt(
        self, run: OzonAnalyticsFunnelRun, now: datetime
    ) -> OzonAnalyticsReservation: ...
    async def acquire_account(self, account: str, token: str, now: datetime) -> bool: ...
    async def release_account(self, account: str, token: str) -> None: ...
    async def runs(self, store: str) -> tuple[OzonAnalyticsFunnelRun, ...]: ...
    async def last_queue(self, account: str) -> str | None: ...
    async def start_run(self, value: OzonAnalyticsFunnelRun) -> None: ...
    async def checkpoint(
        self, run: OzonAnalyticsFunnelRun, page: OzonAnalyticsFunnelPage, now: datetime
    ) -> OzonAnalyticsFunnelRun: ...
    async def defer_run(
        self,
        run: OzonAnalyticsFunnelRun,
        reason: OzonAnalyticsReason,
        retry_at: datetime,
        now: datetime,
        *,
        failed: bool = False,
        account_wide: bool = False,
        raw_payload: str | None = None,
    ) -> None: ...
