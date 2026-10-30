import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from decimal import DecimalException
from typing import Protocol
from uuid import UUID, uuid4

import httpx

from app.application.ozon_promotions import OzonPromotionsService
from app.application.ports import OzonPromotionEntryStore
from app.config import settings
from app.core.constants import (
    OZON_ENTRY_PENDING,
    OZON_PROMOTIONS,
    HTTPStatus,
    OzonEntryIssue,
    OzonEntryState,
    OzonPromotionStatus,
)
from app.dto.ozon_price_calculator import OzonPriceCalculation, OzonPriceCalculationRequest
from app.dto.ozon_prices import SellerCommissions, SellerPriceItem
from app.dto.ozon_promotion_entry import (
    OzonEntryBusyError,
    OzonEntryEvidence,
    OzonEntryIdentity,
    OzonEntryOperation,
    OzonEntryProductUpdate,
    OzonEntryReceipt,
    OzonEntryRefreshedPrices,
    OzonEntryRequest,
    OzonEntryStart,
    OzonEntryUpdate,
    OzonEntryView,
)
from app.dto.ozon_promotions import (
    OzonBoostRecord,
    OzonPromotionCalculationRequest,
    OzonPromotionItem,
    OzonPromotionMoney,
)
from app.economics.ozon_promotion_calculator import calculate
from app.ozon.api import OzonApiError


class EntryClient(Protocol):
    async def identity(self, article: str) -> OzonEntryIdentity: ...
    async def price(self, article: str, product_id: int) -> SellerPriceItem: ...
    async def update(self, payload: OzonEntryUpdate) -> OzonEntryReceipt: ...


def selected(evidence: OzonEntryEvidence, action_id: int) -> OzonPromotionItem | None:
    return next((item for item in evidence.promotions.items if item.action.id == action_id), None)


def price(evidence: OzonEntryEvidence | None) -> OzonPromotionMoney | None:
    if evidence is None or evidence.prices.price.price is None or not evidence.prices.price.currency_code:
        return None
    return OzonPromotionMoney(
        amount=evidence.prices.price.price, currency=evidence.prices.price.currency_code
    )


def conditions(evidence: OzonEntryEvidence) -> OzonEntryEvidence:
    return evidence.model_copy(
        update={
            "identity": evidence.identity.model_copy(update={"name": ""}),
            "prices": evidence.prices.model_copy(update={"commissions": SellerCommissions()}),
            "promotions": evidence.promotions.model_copy(
                update={
                    "checked_at": datetime.min,
                    "items": tuple(
                        item.model_copy(
                            update={
                                "product": item.product.model_copy(
                                    update={
                                        "website_prices": None,
                                        "stock": None,
                                        "price": None,
                                        "action_price": None,
                                    }
                                )
                                if item.product
                                else None
                            }
                        )
                        for item in sorted(evidence.promotions.items, key=lambda row: row.action.id)
                    ),
                }
            ),
        }
    )


def issue(request: OzonEntryRequest, evidence: OzonEntryEvidence, now: datetime) -> OzonEntryIssue | None:
    item = selected(evidence, request.action_id)
    if item is None:
        return OzonEntryIssue.ACTION_UNAVAILABLE
    if item.issue is not None or item.product is None or item.action.is_voucher_action is None:
        return OzonEntryIssue.INCOMPLETE
    if item.status != OzonPromotionStatus.CANDIDATE:
        return OzonEntryIssue.NOT_CANDIDATE
    if not evidence.promotions.complete:
        return OzonEntryIssue.INCOMPLETE
    action, product = item.action, item.product
    if action.date_start and action.date_end and action.date_start >= action.date_end:
        return OzonEntryIssue.INCOMPLETE
    if action.date_end and action.date_end <= now:
        return OzonEntryIssue.ENDED
    if action.freeze_date and action.freeze_date <= now:
        return OzonEntryIssue.FROZEN
    if product.is_quarantined is None:
        return OzonEntryIssue.INCOMPLETE
    if product.is_quarantined or evidence.identity.is_archived or evidence.identity.is_autoarchived:
        return OzonEntryIssue.QUARANTINED
    if evidence.identity.stocks is None or evidence.identity.stocks.has_stock is None:
        return OzonEntryIssue.STOCK_UNKNOWN
    if not evidence.identity.stocks.has_stock:
        return OzonEntryIssue.NO_STOCK
    current = price(evidence)
    if current is None or current.amount <= 0:
        return OzonEntryIssue.PRICE_UNAVAILABLE
    if request.currency != OZON_PROMOTIONS.currency or current.currency != request.currency:
        return OzonEntryIssue.CURRENCY
    limits = tuple(value for value in (product.max_action_price, product.price_min_elastic) if value)
    if not limits:
        return OzonEntryIssue.INCOMPLETE
    minimum, maximum = product.price_min_elastic, product.price_max_elastic
    if (minimum is None) != (maximum is None):
        return OzonEntryIssue.INCOMPLETE
    if minimum and maximum and (maximum.currency != minimum.currency or maximum.amount > minimum.amount):
        return OzonEntryIssue.INCOMPLETE
    if any(value.currency != request.currency for value in limits):
        return OzonEntryIssue.CURRENCY
    if any(request.price > value.amount for value in limits):
        return OzonEntryIssue.PRICE_LIMIT
    if action.is_voucher_action:
        if request.stock is None:
            return OzonEntryIssue.STOCK_REQUIRED
        if product.min_stock is None:
            return OzonEntryIssue.INCOMPLETE
        if request.stock < product.min_stock:
            return OzonEntryIssue.STOCK_LIMIT
    elif request.stock is not None:
        return OzonEntryIssue.STOCK_UNSUPPORTED
    return None


class OzonPromotionEntryService:
    def __init__(
        self,
        repository: OzonPromotionEntryStore,
        client: EntryClient,
        promotions: OzonPromotionsService,
        boost: Callable[[], Awaitable[OzonBoostRecord]],
        calculator: Callable[[OzonPriceCalculationRequest], OzonPriceCalculation],
        lock: Callable[[str], AbstractAsyncContextManager[None]],
        refresh: Callable[[OzonEntryEvidence], Awaitable[OzonEntryRefreshedPrices]],
        clock: Callable[[], datetime],
    ):
        self.repository, self.client, self.promotions = repository, client, promotions
        self.boost, self.calculator, self.lock, self.refresh, self.clock = (
            boost,
            calculator,
            lock,
            refresh,
            clock,
        )

    async def evidence(self, article: str) -> OzonEntryEvidence:
        async with asyncio.timeout(settings.ozon_promotions_timeout_seconds):
            identity = await self.client.identity(article)
            prices = await self.client.price(article, identity.id)
            promotions = await self.promotions.promotions(article, fresh=True)
            if promotions.product_id != identity.id:
                raise ValueError("Promotion identity changed")
            return OzonEntryEvidence(identity=identity, prices=prices, promotions=promotions)

    @staticmethod
    def view(operation: OzonEntryOperation) -> OzonEntryView:
        request = operation.request
        return OzonEntryView(
            id=operation.id,
            store=request.store,
            article=request.article,
            action_id=request.action_id,
            state=operation.state,
            price=OzonPromotionMoney(amount=request.price, currency=request.currency),
            stock=request.stock,
            current_price=price(operation.evidence),
            evidence=operation.evidence,
            observed=operation.observed,
            calculation=operation.calculation,
            refreshed=operation.refreshed,
            issue=operation.issue,
            reason=operation.reason,
            created_at=operation.created_at,
            warnings=tuple(item.reason for item in operation.receipt.warnings) if operation.receipt else (),
            updated_at=operation.updated_at,
            expires_at=operation.expires_at,
        )

    async def owned(self, operation_id: UUID, user_id: int, store: str) -> OzonEntryOperation:
        operation = await self.repository.get(operation_id)
        if operation.user_id != user_id or operation.request.store != store:
            raise LookupError("Entry operation not found")
        return operation

    async def preview(self, request: OzonEntryRequest, user_id: int, user_name: str) -> OzonEntryView:
        now = self.clock()
        operation = OzonEntryOperation(
            id=uuid4(),
            user_id=user_id,
            user_name=user_name,
            request=request,
            state=OzonEntryState.BLOCKED,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=settings.ozon_promotion_entry_preview_seconds),
        )
        try:
            evidence = await self.evidence(request.article)
            item = selected(evidence, request.action_id)
            problem = issue(request, evidence, self.clock())
            calculation = (
                calculate(
                    OzonPromotionCalculationRequest(
                        product=item.product,
                        price=request.price,
                        currency=request.currency,
                        values=request.values,
                        prices=request.prices,
                    ),
                    self.calculator,
                    (await self.boost()).values,
                )
                if item and item.product and request.currency == OZON_PROMOTIONS.currency
                else None
            )
            state = OzonEntryState.BLOCKED if problem else OzonEntryState.PREPARED
            if item and item.issue is None and item.status == OzonPromotionStatus.PARTICIPATING:
                state, problem = OzonEntryState.ALREADY, None
            operation = operation.model_copy(
                update={"evidence": evidence, "calculation": calculation, "state": state, "issue": problem}
            )
        except (OzonApiError, ValueError, TimeoutError, LookupError, DecimalException) as error:
            operation = operation.model_copy(update={"issue": self.read_issue(error)})
        await self.repository.create(operation)
        return self.view(operation)

    @staticmethod
    def read_issue(error: Exception) -> OzonEntryIssue:
        return (
            OzonEntryIssue.ACCESS_DENIED
            if isinstance(error, OzonApiError)
            and error.status in (HTTPStatus.FORBIDDEN, HTTPStatus.UNAUTHORIZED)
            else OzonEntryIssue.UNAVAILABLE
        )

    async def finish(
        self,
        operation: OzonEntryOperation,
        state: OzonEntryState,
        problem: OzonEntryIssue | None = None,
        *,
        release: bool = True,
    ) -> OzonEntryOperation:
        operation = operation.model_copy(
            update={"state": state, "issue": problem, "updated_at": self.clock()}
        )
        await self.repository.save(operation, release=release)
        return operation

    async def begin(self, operation: OzonEntryOperation) -> OzonEntryStart:
        try:
            async with self.lock(operation.request.article):
                return await self._begin(await self.repository.get(operation.id))
        except OzonEntryBusyError:
            current = await self.repository.get(operation.id)
            if current.state != OzonEntryState.PREPARED:
                return OzonEntryStart(view=self.view(current), start=False)
            raise

    async def _begin(self, operation: OzonEntryOperation) -> OzonEntryStart:
        if operation.state != OzonEntryState.PREPARED:
            return OzonEntryStart(view=self.view(operation), start=False)
        if operation.expires_at <= self.clock():
            operation = await self.finish(operation, OzonEntryState.EXPIRED, OzonEntryIssue.EXPIRED)
            return OzonEntryStart(view=self.view(operation), start=False)
        checking = operation.model_copy(update={"state": OzonEntryState.CHECKING, "updated_at": self.clock()})
        if await self.repository.claim(checking):
            return OzonEntryStart(view=self.view(checking), start=True)
        current = await self.repository.get(operation.id)
        if current.state == OzonEntryState.PREPARED:
            raise OzonEntryBusyError()
        return OzonEntryStart(view=self.view(current), start=False)

    async def execute(self, operation_id: UUID) -> None:
        operation = await self.repository.get(operation_id)
        try:
            async with self.lock(operation.request.article):
                operation = await self.repository.get(operation_id)
                if operation.state != OzonEntryState.CHECKING:
                    return
                try:
                    observed = await self.evidence(operation.request.article)
                    item = selected(observed, operation.request.action_id)
                    operation = operation.model_copy(update={"observed": observed})
                    if item and item.issue is None and item.status == OzonPromotionStatus.PARTICIPATING:
                        await self.finish(operation, OzonEntryState.ALREADY)
                        return
                    if operation.expires_at <= self.clock():
                        await self.finish(operation, OzonEntryState.EXPIRED, OzonEntryIssue.EXPIRED)
                        return
                    problem = issue(operation.request, observed, self.clock())
                    if (
                        problem
                        or operation.evidence is None
                        or conditions(observed) != conditions(operation.evidence)
                    ):
                        await self.finish(
                            operation, OzonEntryState.CHANGED, problem or OzonEntryIssue.CHANGED
                        )
                        return
                except (OzonApiError, ValueError, LookupError, TimeoutError) as error:
                    await self.finish(operation, OzonEntryState.FAILED, self.read_issue(error))
                    return
                request = operation.request
                operation = operation.model_copy(update={"sent_at": self.clock()})
                operation = await self.finish(operation, OzonEntryState.SENDING, release=False)
                try:
                    receipt = await self.client.update(
                        OzonEntryUpdate(
                            action_id=request.action_id,
                            products=(
                                OzonEntryProductUpdate(
                                    product_id=observed.identity.id,
                                    action_price=OzonPromotionMoney(
                                        amount=request.price, currency=request.currency
                                    ),
                                    stock=request.stock,
                                ),
                            ),
                        )
                    )
                    operation = operation.model_copy(update={"receipt": receipt})
                    rejected = next(
                        (item for item in receipt.rejected if item.product_id == observed.identity.id), None
                    )
                    if rejected:
                        operation = operation.model_copy(update={"reason": rejected.reason})
                        await self.finish(operation, OzonEntryState.REJECTED, OzonEntryIssue.REJECTED)
                        return
                    if (
                        observed.identity.id not in receipt.active_product_ids
                        or observed.identity.id in receipt.deactivated_product_ids
                    ):
                        raise ValueError("Unconfirmed promotion receipt")
                    operation = await self.finish(operation, OzonEntryState.VERIFYING, release=False)
                except (OzonApiError, httpx.TransportError, TimeoutError, ValueError) as error:
                    if isinstance(error, OzonApiError) and error.status in (
                        HTTPStatus.BAD_REQUEST,
                        HTTPStatus.UNAUTHORIZED,
                        HTTPStatus.FORBIDDEN,
                        HTTPStatus.NOT_FOUND,
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                    ):
                        await self.finish(
                            operation,
                            OzonEntryState.REJECTED,
                            self.read_issue(error)
                            if error.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)
                            else OzonEntryIssue.REJECTED,
                        )
                        return
                    operation = await self.finish(
                        operation, OzonEntryState.UNCERTAIN, OzonEntryIssue.WRITE_UNCERTAIN, release=False
                    )
                finally:
                    self.promotions.invalidate(request.article)
                await self.verify(operation)
        except OzonEntryBusyError:
            return

    async def verify(self, operation: OzonEntryOperation) -> OzonEntryOperation:
        try:
            observed = await self.evidence(operation.request.article)
            operation = operation.model_copy(update={"observed": observed})
            item = selected(observed, operation.request.action_id)
            expected = OzonPromotionMoney(amount=operation.request.price, currency=operation.request.currency)
            actual = (
                item.product.action_price
                if item and item.product and item.action.is_voucher_action
                else price(observed)
            )
            if (
                item
                and item.issue is None
                and item.status == OzonPromotionStatus.PARTICIPATING
                and actual == expected
            ):
                operation = operation.model_copy(update={"refreshed": await self.refresh(observed)})
                return await self.finish(operation, OzonEntryState.SUCCEEDED)
            return await self.finish(
                operation,
                OzonEntryState.VERIFYING if operation.receipt else OzonEntryState.UNCERTAIN,
                OzonEntryIssue.VERIFY_FAILED,
                release=False,
            )
        except (OzonApiError, ValueError, LookupError, TimeoutError):
            return await self.finish(
                operation, OzonEntryState.UNCERTAIN, OzonEntryIssue.VERIFY_FAILED, release=False
            )

    async def check(self, operation: OzonEntryOperation) -> OzonEntryView:
        if operation.state not in OZON_ENTRY_PENDING:
            return self.view(operation)
        try:
            async with self.lock(operation.request.article):
                operation = await self.repository.get(operation.id)
                if operation.sent_at is not None and operation.state in (
                    OzonEntryState.SENDING,
                    OzonEntryState.VERIFYING,
                    OzonEntryState.UNCERTAIN,
                ):
                    operation = await self.verify(operation)
                elif operation.state == OzonEntryState.CHECKING and operation.expires_at <= self.clock():
                    operation = await self.finish(
                        operation, OzonEntryState.FAILED, OzonEntryIssue.INTERRUPTED
                    )
        except OzonEntryBusyError:
            operation = await self.repository.get(operation.id)
        return self.view(operation)
