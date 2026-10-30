import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from decimal import DecimalException
from uuid import UUID, uuid4

import httpx

from app.application.ozon_promotions import OzonPromotionsService
from app.application.ports import OzonPriceChangeClient, OzonPriceChangeStore
from app.config import settings
from app.core.constants import (
    OZON_PRICE_CHANGE_PENDING,
    OZON_PRICE_CHANGES,
    HTTPStatus,
    OzonPriceChangeIssue,
    OzonPriceChangeState,
)
from app.dto.ozon_price_changes import (
    OzonPriceChangeBusyError,
    OzonPriceChangeEvidence,
    OzonPriceChangeItem,
    OzonPriceChangeOperation,
    OzonPriceChangePayload,
    OzonPriceChangeRequest,
    OzonPriceChangeStart,
    OzonPriceChangeView,
)
from app.dto.ozon_prices import PriceValues
from app.economics.ozon_price_changes import changed, issue, plan
from app.ozon.api import OzonApiError

logger = logging.getLogger(__name__)


class OzonPriceChangeService:
    def __init__(
        self,
        repository: OzonPriceChangeStore,
        client: OzonPriceChangeClient,
        promotions: OzonPromotionsService,
        lock: Callable[[str], AbstractAsyncContextManager[None]],
        refresh: Callable[[OzonPriceChangeEvidence], Awaitable[PriceValues]],
        clock: Callable[[], datetime],
    ):
        self.repository, self.client, self.promotions = repository, client, promotions
        self.lock, self.refresh, self.clock = lock, refresh, clock

    async def evidence(self, article: str) -> OzonPriceChangeEvidence:
        async with asyncio.timeout(settings.ozon_promotions_timeout_seconds):
            identity = await self.client.identity(article)
            promotions = await self.promotions.promotions(article, fresh=True)
            prices = await self.client.price(article, identity.id)
            if (
                prices.product_id != identity.id
                or prices.offer_id != article
                or promotions.product_id != identity.id
            ):
                raise ValueError("Unexpected price identity")
            return OzonPriceChangeEvidence(
                identity=identity, prices=prices, promotions=promotions, received_at=self.clock()
            )

    @staticmethod
    def view(operation: OzonPriceChangeOperation) -> OzonPriceChangeView:
        return OzonPriceChangeView(
            id=operation.id,
            store=operation.request.store,
            article=operation.request.article,
            state=operation.state,
            target_field=operation.request.target_field,
            target_price=operation.request.target_price,
            user_name=operation.user_name,
            plan=operation.plan,
            observed=operation.observed,
            issue=operation.issue,
            refreshed=operation.refreshed,
            sent_at=operation.sent_at,
            errors=tuple(error for item in operation.receipt.result for error in item.errors)
            if operation.receipt
            else (),
            created_at=operation.created_at,
            updated_at=operation.updated_at,
            expires_at=operation.expires_at,
        )

    async def owned(self, operation_id: UUID, user_id: int, store: str) -> OzonPriceChangeOperation:
        operation = await self.repository.get(operation_id)
        if operation.user_id != user_id or operation.request.store != store:
            raise LookupError("Price operation not found")
        return operation

    @staticmethod
    def read_issue(error: Exception) -> OzonPriceChangeIssue:
        return (
            OzonPriceChangeIssue.ACCESS_DENIED
            if isinstance(error, OzonApiError)
            and error.status
            in (
                HTTPStatus.UNAUTHORIZED,
                HTTPStatus.FORBIDDEN,
            )
            else OzonPriceChangeIssue.UNAVAILABLE
        )

    async def preview(
        self, request: OzonPriceChangeRequest, user_id: int, user_name: str
    ) -> OzonPriceChangeView:
        now = self.clock()
        operation = OzonPriceChangeOperation(
            id=uuid4(),
            user_id=user_id,
            user_name=user_name,
            request=request,
            state=OzonPriceChangeState.BLOCKED,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=settings.ozon_price_change_preview_seconds),
        )
        try:
            evidence = await self.evidence(request.article)
            prepared = plan(request, evidence)
            problem = issue(evidence, prepared, self.clock())
            state = OzonPriceChangeState.BLOCKED if problem else OzonPriceChangeState.PREPARED
            if not problem and prepared and prepared.price == prepared.previous_price:
                state = OzonPriceChangeState.UNCHANGED
            operation = operation.model_copy(
                update={
                    "evidence": evidence,
                    "plan": prepared,
                    "issue": problem,
                    "state": state,
                    "updated_at": self.clock(),
                }
            )
        except (
            OzonApiError,
            ValueError,
            LookupError,
            TimeoutError,
            DecimalException,
            httpx.TransportError,
        ) as error:
            operation = operation.model_copy(update={"issue": self.read_issue(error)})
        await self.repository.create(operation)
        return self.view(operation)

    async def finish(
        self,
        operation: OzonPriceChangeOperation,
        state: OzonPriceChangeState,
        problem: OzonPriceChangeIssue | None = None,
    ) -> OzonPriceChangeOperation:
        updated = operation.model_copy(update={"state": state, "issue": problem, "updated_at": self.clock()})
        await self.repository.save(updated, release=state not in OZON_PRICE_CHANGE_PENDING)
        return updated

    async def begin(self, operation: OzonPriceChangeOperation) -> OzonPriceChangeStart:
        if operation.state != OzonPriceChangeState.PREPARED:
            return OzonPriceChangeStart(view=self.view(operation), start=False)
        try:
            return await self._begin(operation)
        except OzonPriceChangeBusyError:
            current = await self.repository.get(operation.id)
            if current.state != OzonPriceChangeState.PREPARED:
                return OzonPriceChangeStart(view=self.view(current), start=False)
            raise

    async def _begin(self, operation: OzonPriceChangeOperation) -> OzonPriceChangeStart:
        async with self.lock(operation.request.article):
            operation = await self.repository.get(operation.id)
            if operation.state != OzonPriceChangeState.PREPARED:
                return OzonPriceChangeStart(view=self.view(operation), start=False)
            if operation.expires_at <= self.clock():
                operation = await self.finish(
                    operation, OzonPriceChangeState.EXPIRED, OzonPriceChangeIssue.EXPIRED
                )
                return OzonPriceChangeStart(view=self.view(operation), start=False)
            checking = operation.model_copy(
                update={"state": OzonPriceChangeState.CHECKING, "updated_at": self.clock()}
            )
            if not await self.repository.claim(checking):
                raise OzonPriceChangeBusyError()
            return OzonPriceChangeStart(view=self.view(checking), start=True)

    async def execute(self, operation_id: UUID) -> None:
        operation = await self.repository.get(operation_id)
        try:
            async with self.lock(operation.request.article):
                operation = await self.repository.get(operation.id)
                if operation.state != OzonPriceChangeState.CHECKING:
                    return
                try:
                    observed = await self.evidence(operation.request.article)
                    operation = operation.model_copy(update={"observed": observed})
                    if operation.expires_at <= self.clock():
                        await self.finish(
                            operation, OzonPriceChangeState.EXPIRED, OzonPriceChangeIssue.EXPIRED
                        )
                        return
                    problem = issue(observed, operation.plan, self.clock())
                    if problem or operation.evidence is None or changed(operation.evidence, observed):
                        await self.finish(
                            operation, OzonPriceChangeState.CHANGED, problem or OzonPriceChangeIssue.CHANGED
                        )
                        return
                    attempts = await self.repository.attempts(
                        operation.request.store,
                        operation.request.article,
                        self.clock() - timedelta(seconds=OZON_PRICE_CHANGES.rate_window_seconds),
                    )
                    if attempts >= OZON_PRICE_CHANGES.updates_per_hour:
                        await self.finish(
                            operation, OzonPriceChangeState.BLOCKED, OzonPriceChangeIssue.RATE_LIMIT
                        )
                        return
                    prepared = operation.plan
                    if prepared is None:
                        raise ValueError("Missing price plan")
                    operation = operation.model_copy(update={"sent_at": self.clock()})
                    operation = await self.finish(operation, OzonPriceChangeState.SENDING)
                    receipt = await self.client.update(
                        OzonPriceChangePayload(
                            prices=(
                                OzonPriceChangeItem(
                                    product_id=observed.identity.id,
                                    price=prepared.price,
                                    old_price=prepared.old_price,
                                    min_price=prepared.min_price,
                                    currency_code=prepared.currency,
                                ),
                            )
                        )
                    )
                    operation = operation.model_copy(update={"receipt": receipt})
                    if receipt.result[0].offer_id != operation.request.article:
                        raise ValueError("Unexpected price receipt identity")
                    if receipt.result[0].errors:
                        await self.finish(
                            operation, OzonPriceChangeState.REJECTED, OzonPriceChangeIssue.REJECTED
                        )
                        return
                    operation = await self.finish(operation, OzonPriceChangeState.VERIFYING)
                except OzonApiError as error:
                    if operation.sent_at is None:
                        await self.finish(operation, OzonPriceChangeState.FAILED, self.read_issue(error))
                        return
                    if error.status in (
                        HTTPStatus.BAD_REQUEST,
                        HTTPStatus.UNAUTHORIZED,
                        HTTPStatus.FORBIDDEN,
                        HTTPStatus.NOT_FOUND,
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        HTTPStatus.TOO_MANY_REQUESTS,
                    ):
                        await self.finish(
                            operation,
                            OzonPriceChangeState.REJECTED,
                            OzonPriceChangeIssue.RATE_LIMIT
                            if error.status == HTTPStatus.TOO_MANY_REQUESTS
                            else self.read_issue(error),
                        )
                        return
                    operation = await self.finish(
                        operation, OzonPriceChangeState.UNCERTAIN, OzonPriceChangeIssue.WRITE_UNCERTAIN
                    )
                except Exception:
                    logger.exception("ozon_price_change_failed operation=%s", operation.id)
                    if operation.sent_at is None:
                        await self.finish(
                            operation, OzonPriceChangeState.FAILED, OzonPriceChangeIssue.UNAVAILABLE
                        )
                        return
                    operation = await self.finish(
                        operation, OzonPriceChangeState.UNCERTAIN, OzonPriceChangeIssue.WRITE_UNCERTAIN
                    )
                finally:
                    self.promotions.invalidate(operation.request.article)
                for attempt in range(settings.ozon_price_change_verify_attempts):
                    operation = await self.verify(operation)
                    if operation.state == OzonPriceChangeState.SUCCEEDED:
                        break
                    if attempt + 1 < settings.ozon_price_change_verify_attempts:
                        await asyncio.sleep(settings.ozon_price_change_verify_interval_seconds)
        except OzonPriceChangeBusyError:
            return
        except asyncio.CancelledError:
            await asyncio.shield(
                self.finish(
                    operation,
                    OzonPriceChangeState.UNCERTAIN if operation.sent_at else OzonPriceChangeState.FAILED,
                    OzonPriceChangeIssue.WRITE_UNCERTAIN
                    if operation.sent_at
                    else OzonPriceChangeIssue.INTERRUPTED,
                )
            )
            raise

    async def verify(self, operation: OzonPriceChangeOperation) -> OzonPriceChangeOperation:
        try:
            observed = await self.evidence(operation.request.article)
            operation = operation.model_copy(update={"observed": observed})
            if (
                operation.plan
                and observed.promotions.complete
                and observed.prices.price.price == operation.plan.price
                and observed.prices.price.currency_code == operation.plan.currency
            ):
                operation = operation.model_copy(update={"refreshed": await self.refresh(observed)})
                return await self.finish(operation, OzonPriceChangeState.SUCCEEDED)
        except Exception:
            logger.exception("ozon_price_change_verify_failed operation=%s", operation.id)
        return await self.finish(
            operation, OzonPriceChangeState.UNCERTAIN, OzonPriceChangeIssue.VERIFY_FAILED
        )

    async def check(self, operation: OzonPriceChangeOperation) -> OzonPriceChangeView:
        if operation.state not in OZON_PRICE_CHANGE_PENDING:
            return self.view(operation)
        try:
            async with self.lock(operation.request.article):
                operation = await self.repository.get(operation.id)
                if operation.state not in OZON_PRICE_CHANGE_PENDING:
                    return self.view(operation)
                if operation.sent_at:
                    operation = await self.verify(operation)
                elif operation.expires_at <= self.clock():
                    operation = await self.finish(
                        operation, OzonPriceChangeState.FAILED, OzonPriceChangeIssue.INTERRUPTED
                    )
        except OzonPriceChangeBusyError:
            operation = await self.repository.get(operation.id)
        return self.view(operation)
