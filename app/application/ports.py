from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import date, datetime
from types import TracebackType
from typing import TYPE_CHECKING, Protocol

from app.dto.fbo_stock import (
    FboActor,
    FboEvidence,
    FboMovement,
    FboStockCommand,
    FboStockReport,
    FboStockResult,
    FboTarget,
)

if TYPE_CHECKING:
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
    User,
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
from app.dto.ozon_storage import StorageEvidence, StoragePeriod, StorageReportTicket, StorageStoreSnapshot
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


class WbRatingWriter(Protocol):
    def upsert(
        self,
        observations: tuple[WbRatingObservation, ...],
        valid_ratings: tuple[WbRatingObservation, ...],
    ) -> int: ...
from app.dto.wb_standard_costs import PublishSnapshot


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
    async def settings(self, stores: tuple[str, ...]) -> tuple[OzonCabinetRecord, ...]: ...

    async def save(self, command: OzonCabinetSave) -> OzonCabinetRecord: ...

    async def save_buyout(self, snapshot: OzonBuyoutSnapshot) -> None: ...

    async def fail_buyout(self, store: str, error: str) -> None: ...


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
