from __future__ import annotations

from types import TracebackType
from typing import Protocol

from app.dto.decision import DecisionAction, SetDecisionStatusCommand
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
    PermissionChange,
    SessionData,
    SessionToken,
    User,
    UserAccessPolicyChange,
    UserActiveChange,
    UserCollection,
    UserCountQuery,
    UserId,
    UserPasswordChange,
    UserRoleChange,
    UserSectionAccessChange,
    UserStoreAccessChange,
    WbTokenInfoCollection,
    WbTokenInfoCommand,
)
from app.dto.rnp import (
    AddRnpActionCommand,
    RnpAction,
    RnpArticleExists,
    RnpArticleQuery,
    RnpStrategy,
    SaveRnpStrategyCommand,
)
from app.dto.stock import (
    ApplyShipmentCommand,
    ApplyTransferCommand,
    CancelTransitCommand,
    CatalogItems,
    CatalogQuery,
    ReceiveTransitCommand,
    StockIncrement,
    StockQuantity,
    StockQuantityQuery,
    TransitActionResult,
)


class IdentityRepository(Protocol):
    def create_user(self, command: CreateUserCommand) -> UserId: ...

    def get_user(self, query: UserId) -> User | None: ...

    def get_user_by_login(self, query: LoginQuery) -> User | None: ...

    def list_users(self) -> UserCollection: ...

    def count_users(self) -> CountResult: ...

    def count_superadmins(self, query: UserCountQuery) -> CountResult: ...

    def set_active(self, command: UserActiveChange) -> None: ...

    def set_permission(self, command: PermissionChange) -> None: ...

    def set_store_access(self, command: UserStoreAccessChange) -> None: ...

    def set_role(self, command: UserRoleChange) -> None: ...

    def set_section_access(self, command: UserSectionAccessChange) -> None: ...

    def set_access_policy(self, command: UserAccessPolicyChange) -> None: ...

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


class RnpRepository(Protocol):
    def article_exists(self, query: RnpArticleQuery) -> RnpArticleExists: ...

    def save_strategy(self, command: SaveRnpStrategyCommand) -> RnpStrategy: ...

    def add_action(self, command: AddRnpActionCommand) -> RnpAction: ...


class RnpUnitOfWork(Protocol):
    repository: RnpRepository

    def __enter__(self) -> RnpUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def commit(self) -> None: ...


class RnpUnitOfWorkFactory(Protocol):
    def __call__(self) -> RnpUnitOfWork: ...


class DecisionRepository(Protocol):
    def set_status(self, command: SetDecisionStatusCommand) -> DecisionAction: ...


class DecisionUnitOfWork(Protocol):
    repository: DecisionRepository

    def __enter__(self) -> DecisionUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def commit(self) -> None: ...


class DecisionUnitOfWorkFactory(Protocol):
    def __call__(self) -> DecisionUnitOfWork: ...


class StockRepository(Protocol):
    def catalog(self, query: CatalogQuery) -> CatalogItems: ...

    def quantity(self, query: StockQuantityQuery) -> StockQuantity: ...

    def increment(self, command: StockIncrement) -> None: ...

    def apply_transfer(self, command: ApplyTransferCommand) -> int: ...

    def receive_transfer(self, command: ReceiveTransitCommand) -> TransitActionResult: ...

    def cancel_transfer(self, command: CancelTransitCommand) -> TransitActionResult: ...

    def apply_shipment(self, command: ApplyShipmentCommand) -> None: ...


class StockUnitOfWork(Protocol):
    repository: StockRepository

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
