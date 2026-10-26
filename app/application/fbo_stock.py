import re
from collections import defaultdict
from datetime import UTC, datetime

from app.application.ports import FboStockRepository
from app.core.constants import (
    FBO_STOCK,
    FboStockAction,
    FboStockErrorCode,
    FboStockIssue,
    FboStockState,
)
from app.dto.fbo_stock import (
    FboActor,
    FboAllocation,
    FboBalance,
    FboEvidence,
    FboMovement,
    FboStockCommand,
    FboStockError,
    FboStockResult,
    FboTarget,
)
from app.dto.inbound_supplies import InboundSupply
from app.stock.fbo_availability import (
    allocations_match,
    is_accepted,
    is_active,
    movement_availability,
    supply_shortages,
)


def allocate_supply(
    supply: InboundSupply,
    balances: tuple[FboBalance, ...],
    previous: FboMovement,
) -> FboMovement:
    requested: dict[str, int] = defaultdict(int)
    for item in supply.items:
        requested[item.article] += item.quantity
    fulfillment = previous.fulfillment
    if not fulfillment:
        raise FboStockError(FboStockErrorCode.FULFILLMENT_REQUIRED)
    if any(row.fulfillment != fulfillment for row in previous.allocations):
        raise FboStockError(FboStockErrorCode.FULFILLMENT_LOCKED)
    shortages = supply_shortages(supply, balances, previous.allocations, fulfillment=fulfillment)
    if shortages:
        return previous.model_copy(
            update={
                "supply": supply,
                "shortages": shortages,
                "manual_required": True,
                "issue": FboStockIssue.SHORTAGE,
                "fulfillment": fulfillment if previous.posted_at else "",
                "checked_fulfillment": fulfillment,
            }
        )
    items = {item.article: item for item in supply.items}
    allocations = tuple(
        FboAllocation(
            article=article,
            fulfillment=fulfillment,
            quantity=quantity,
            name=items[article].name,
            barcode=items[article].barcode,
        )
        for article, quantity in sorted(requested.items())
        if quantity
    )
    return previous.model_copy(
        update={
            "supply": supply,
            "allocations": tuple(allocations),
            "shortages": (),
            "manual_required": False,
            "issue": FboStockIssue.NONE,
        }
    )


class FboStockService:
    def __init__(self, repository: FboStockRepository) -> None:
        self.repository = repository

    async def sync(self, target: FboTarget) -> None:
        async with self.repository.transaction(target) as unit:
            evidence = await unit.evidence()
            known = {row.supply.key: row for row in evidence.movements}
            supplies = {row.key: row for row in evidence.supplies}
            for saved in evidence.movements:
                if saved.supply.key not in supplies and saved.allocations:
                    supplies[saved.supply.key] = saved.supply.model_copy(update={"unavailable": True})
            for supply in sorted(supplies.values(), key=lambda row: (row.created_at, row.key)):
                old = known.get(supply.key)
                movement = self._plan(target, supply, old, evidence)
                movement = movement_availability(target, movement, evidence)
                movement = await unit.save(old, movement, FboActor())
                balances = {(row.fulfillment, row.article): row for row in evidence.balances}
                for allocation in old.allocations if old else ():
                    identity = (allocation.fulfillment, allocation.article)
                    balance = balances.get(
                        identity,
                        FboBalance(
                            article=allocation.article,
                            fulfillment=allocation.fulfillment,
                            quantity=0,
                        ),
                    )
                    balances[identity] = balance.model_copy(
                        update={"quantity": balance.quantity + allocation.quantity}
                    )
                for allocation in movement.allocations:
                    identity = (allocation.fulfillment, allocation.article)
                    balance = balances[identity]
                    balances[identity] = balance.model_copy(
                        update={"quantity": balance.quantity - allocation.quantity}
                    )
                known[supply.key] = movement
                evidence = evidence.model_copy(
                    update={"balances": tuple(balances.values()), "movements": tuple(known.values())}
                )

    def _plan(
        self,
        target: FboTarget,
        supply: InboundSupply,
        previous: FboMovement | None,
        evidence: FboEvidence,
        *,
        force: bool = False,
    ) -> FboMovement:
        now = datetime.now(UTC).isoformat()
        old = previous or FboMovement(supply=supply)
        current = old.model_copy(update={"supply": supply, "updated_at": now, "can_post": False})
        current = movement_availability(target, current, evidence)
        if not evidence.confirmed or supply.unavailable or supply.stage == FBO_STOCK.stage_unknown:
            return current.model_copy(update={"issue": FboStockIssue.UNCONFIRMED})
        if supply.stage == FBO_STOCK.stage_cancelled:
            return current.model_copy(
                update={
                    "issue": FboStockIssue.CANCELLED if old.allocations else FboStockIssue.NONE,
                    "state": old.state if old.allocations else FboStockState.CANCELLED,
                }
            )
        accepted = is_accepted(target, supply)
        if accepted and not old.posted_at and not old.allocations:
            return current.model_copy(update={"state": FboStockState.HISTORICAL})
        if not accepted and not is_active(target, supply):
            return current.model_copy(
                update={
                    "issue": FboStockIssue.UNCONFIRMED if old.allocations else FboStockIssue.NONE,
                }
            )
        articles = {item.article for item in supply.items if item.quantity}
        overlaps = any(
            articles.intersection(item.article for item in batch.items) for batch in evidence.legacy
        )
        parent_held = any(
            row.supply.key == supply.parent_key and row.allocations for row in evidence.movements
        )
        if not old.posted_at and (overlaps or parent_held):
            return current.model_copy(update={"issue": FboStockIssue.LEGACY, "manual_required": True})
        if not force:
            changed = bool(old.posted_at) and not allocations_match(current)
            return current.model_copy(
                update={
                    "state": (FboStockState.ACCEPTED if accepted else FboStockState.ACTIVE)
                    if old.posted_at
                    else FboStockState.PENDING,
                    "manual_required": changed,
                    "issue": FboStockIssue.SHORTAGE if current.shortages else FboStockIssue.NONE,
                }
            )
        planned = allocate_supply(supply, evidence.balances, current)
        if planned.issue == FboStockIssue.SHORTAGE:
            if accepted and old.posted_at:
                planned = planned.model_copy(update={"state": FboStockState.ACCEPTED})
            return planned
        return planned.model_copy(
            update={
                "state": FboStockState.ACCEPTED if accepted else FboStockState.ACTIVE,
                "posted_at": old.posted_at or now,
            }
        )

    async def execute(self, command: FboStockCommand) -> FboStockResult:
        if not re.fullmatch(FBO_STOCK.request_pattern, command.request_key) or command.user_id <= 0:
            raise FboStockError(FboStockErrorCode.INVALID_REQUEST)
        async with self.repository.transaction(command.target) as unit:
            replay = await unit.claim(command)
            if replay is not None:
                return replay
            evidence = await unit.evidence()
            old = next(
                (row for row in evidence.movements if row.supply.key == command.change.supply_key), None
            )
            supply = next((row for row in evidence.supplies if row.key == command.change.supply_key), None)
            if old is None or supply is None:
                raise FboStockError(FboStockErrorCode.MISSING)
            if not evidence.confirmed and command.change.action != FboStockAction.SELECT_FULFILLMENT:
                raise FboStockError(FboStockErrorCode.UNCONFIRMED)
            old = movement_availability(command.target, old, evidence)
            actor = FboActor(id=command.user_id, name=command.user_name)
            adopt: tuple[int, ...] = ()
            if command.change.action == FboStockAction.SELECT_FULFILLMENT:
                if old.posted_at or old.allocations:
                    raise FboStockError(FboStockErrorCode.FULFILLMENT_LOCKED)
                if not is_active(command.target, supply):
                    raise FboStockError(FboStockErrorCode.UNCONFIRMED)
                fulfillment = command.change.fulfillment
                if fulfillment and fulfillment not in evidence.fulfillments:
                    raise FboStockError(FboStockErrorCode.INVALID_FULFILLMENT)
                if fulfillment and (
                    not supply.quantity or any(not item.article for item in supply.items if item.quantity)
                ):
                    raise FboStockError(FboStockErrorCode.UNCONFIRMED)
                candidate = old.model_copy(
                    update={
                        "supply": supply,
                        "fulfillment": fulfillment,
                        "checked_fulfillment": fulfillment,
                        "shortages": (),
                        "manual_required": False,
                    }
                )
                movement = self._plan(command.target, supply, candidate, evidence)
                if movement.shortages:
                    movement = movement.model_copy(update={"fulfillment": ""})
            elif command.change.action == FboStockAction.LINK:
                chosen = tuple(row for row in evidence.legacy if row.id in command.change.batch_ids)
                expected: dict[str, int] = defaultdict(int)
                actual: dict[str, int] = defaultdict(int)
                for item in supply.items:
                    expected[item.article] += item.quantity
                for batch in chosen:
                    for allocation in batch.items:
                        actual[allocation.article] += allocation.quantity
                if (
                    old.allocations
                    or not chosen
                    or len(chosen) != len(command.change.batch_ids)
                    or expected != actual
                    or supply.unavailable
                    or not (is_active(command.target, supply) or is_accepted(command.target, supply))
                ):
                    raise FboStockError(FboStockErrorCode.INVALID_LINK)
                combined: dict[tuple[str, str], FboAllocation] = {}
                for batch in chosen:
                    for allocation in batch.items:
                        identity = (allocation.fulfillment, allocation.article)
                        former = combined.get(identity)
                        combined[identity] = allocation.model_copy(
                            update={
                                "quantity": allocation.quantity + (former.quantity if former else 0),
                            }
                        )
                sources = {allocation.fulfillment for allocation in combined.values()}
                fulfillment = next(iter(sources)) if len(sources) == 1 else ""
                old = old.model_copy(
                    update={
                        "allocations": tuple(combined.values()),
                        "fulfillment": fulfillment,
                        "checked_fulfillment": fulfillment,
                        "posted_at": min(batch.sent_at for batch in chosen),
                        "manual_required": False,
                        "issue": FboStockIssue.NONE,
                    }
                )
                adopt = command.change.batch_ids
                movement = self._plan(command.target, supply, old, evidence)
            elif command.change.action == FboStockAction.RETURN:
                if supply.stage != FBO_STOCK.stage_cancelled or supply.unavailable:
                    raise FboStockError(FboStockErrorCode.INVALID_RETURN)
                movement = old.model_copy(
                    update={
                        "supply": supply,
                        "allocations": (),
                        "state": FboStockState.CANCELLED,
                        "issue": FboStockIssue.NONE,
                        "manual_required": False,
                        "shortages": (),
                        "fulfillment": "",
                        "checked_fulfillment": "",
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                )
            else:
                if not old.fulfillment or command.change.fulfillment != old.fulfillment:
                    raise FboStockError(FboStockErrorCode.FULFILLMENT_REQUIRED)
                if old.fulfillment not in evidence.fulfillments:
                    raise FboStockError(FboStockErrorCode.INVALID_FULFILLMENT)
                if not supply.quantity or any(not item.article for item in supply.items if item.quantity):
                    raise FboStockError(FboStockErrorCode.UNCONFIRMED)
                if supply.unavailable or not (
                    is_active(command.target, supply) or is_accepted(command.target, supply)
                ):
                    raise FboStockError(FboStockErrorCode.UNCONFIRMED)
                movement = self._plan(command.target, supply, old, evidence, force=True)
            if not old.posted_by and movement.posted_at:
                movement = movement.model_copy(update={"posted_by": actor.name})
            movement = movement_availability(command.target, movement, evidence)
            movement = await unit.save(old, movement, actor, adopt=adopt)
            result = FboStockResult(movement=movement)
            await unit.finish(command, result)
            return result
