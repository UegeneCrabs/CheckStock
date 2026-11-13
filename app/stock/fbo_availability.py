from collections import defaultdict

from app.core.constants import FBO_STOCK, FboStockIssue, FboStockState, Marketplace
from app.dto.fbo_stock import FboAllocation, FboBalance, FboEvidence, FboMovement, FboShortage, FboTarget
from app.dto.inbound_supplies import InboundSupply


def supply_shortages(
    supply: InboundSupply,
    balances: tuple[FboBalance, ...],
    allocations: tuple[FboAllocation, ...],
    *,
    fulfillment: str,
) -> tuple[FboShortage, ...]:
    requested = requested_quantities(supply, allocations)
    available: dict[str, int] = defaultdict(int)
    for balance in balances:
        if balance.fulfillment == fulfillment:
            available[balance.article] += max(balance.quantity, 0)
    for allocation in allocations:
        if allocation.fulfillment == fulfillment:
            available[allocation.article] += allocation.quantity
    return tuple(
        FboShortage(
            article=article,
            required=quantity,
            available=available[article],
            missing=quantity - available[article],
        )
        for article, quantity in sorted(requested.items())
        if quantity > available[article]
    )


def movement_shortages(movement: FboMovement, balances: tuple[FboBalance, ...]) -> tuple[FboShortage, ...]:
    fulfillment = movement.fulfillment or movement.checked_fulfillment
    if not fulfillment or not has_confirmed_items(movement.supply):
        return ()
    if movement.state in {FboStockState.HISTORICAL, FboStockState.CANCELLED}:
        return ()
    if movement.state == FboStockState.ACCEPTED and not movement.manual_required:
        return ()
    return supply_shortages(movement.supply, balances, movement.allocations, fulfillment=fulfillment)


def has_confirmed_items(supply: InboundSupply) -> bool:
    return (
        supply.composition_confirmed
        and bool(supply.quantity)
        and all(item.article for item in supply.items if item.quantity)
    )


def is_accepted(target: FboTarget, supply: InboundSupply) -> bool:
    return (
        supply.status
        == {
            "WB": FBO_STOCK.wb_accepted,
            "OZON": FBO_STOCK.ozon_accepted,
            "YANDEX MARKET": FBO_STOCK.yandex_accepted,
        }[target.marketplace]
    )


def is_active(target: FboTarget, supply: InboundSupply) -> bool:
    return (
        supply.status
        in {
            "WB": FBO_STOCK.wb_active,
            "OZON": FBO_STOCK.ozon_active,
            "YANDEX MARKET": FBO_STOCK.yandex_active,
        }[target.marketplace]
    )


def can_select_fulfillment(target: FboTarget, movement: FboMovement) -> bool:
    supply = movement.supply
    if movement.posted_at or movement.allocations or supply.archived or supply.unavailable:
        return False
    if target.marketplace == Marketplace.OZON:
        return supply.status == FBO_STOCK.ozon_data_filling
    return is_active(target, supply) and has_confirmed_items(supply)


def is_automatic_return(target: FboTarget, supply: InboundSupply) -> bool:
    return (target.marketplace == "OZON" and supply.status in FBO_STOCK.ozon_returned) or (
        target.marketplace == "YANDEX MARKET" and supply.status in FBO_STOCK.yandex_returned
    )


def with_accepted_quantities(movement: FboMovement, *, preserve_confirmed: bool = False) -> FboMovement:
    confirmed: dict[str, int] = defaultdict(int)
    reported: dict[str, int] = defaultdict(int)
    for item in movement.supply.items:
        if item.accepted_quantity is not None:
            confirmed[item.article] += item.accepted_quantity
            reported[item.article] += 1
    counts: dict[str, int] = defaultdict(int)
    retained: dict[str, int] = defaultdict(int)
    for item in movement.supply.items:
        counts[item.article] += 1
    for row in movement.allocations:
        retained[row.article] += row.received_quantity
    left = {
        article: quantity
        if reported[article] == counts[article] and not preserve_confirmed
        else max(quantity, retained[article])
        for article, quantity in confirmed.items()
    }
    allocations = []
    for row in movement.allocations:
        accepted = row.received_quantity
        if row.article in left:
            accepted = min(left[row.article], accepted)
            left[row.article] -= accepted
        allocations.append(row.model_copy(update={"received_quantity": accepted}))
    for index, row in enumerate(allocations):
        if row.article not in left:
            continue
        extra = min(left[row.article], row.quantity - row.received_quantity)
        allocations[index] = row.model_copy(update={"received_quantity": row.received_quantity + extra})
        left[row.article] -= extra
    return movement.model_copy(update={"allocations": tuple(allocations)})


def allocations_match(movement: FboMovement) -> bool:
    expected = requested_quantities(movement.supply, movement.allocations)
    actual: dict[str, int] = defaultdict(int)
    for allocation in movement.allocations:
        actual[allocation.article] += allocation.quantity
    return {key: value for key, value in expected.items() if value} == dict(actual)


def requested_quantities(supply: InboundSupply, allocations: tuple[FboAllocation, ...]) -> dict[str, int]:
    expected: dict[str, int] = defaultdict(int)
    accepted: dict[str, int] = defaultdict(int)
    for item in supply.items:
        expected[item.article] += item.quantity
    for allocation in allocations:
        accepted[allocation.article] += allocation.received_quantity
    for article, quantity in accepted.items():
        expected[article] = max(expected[article], quantity)
    return expected


def movement_availability(target: FboTarget, movement: FboMovement, evidence: FboEvidence) -> FboMovement:
    sources = {row.fulfillment for row in movement.allocations}
    fulfillment = movement.fulfillment or (next(iter(sources)) if len(sources) == 1 else "")
    current = movement.model_copy(update={"fulfillment": fulfillment})
    shortages = movement_shortages(current, evidence.balances)
    valid_items = has_confirmed_items(current.supply)
    can_post = (
        evidence.confirmed
        and not current.supply.unavailable
        and current.supply.stage not in {FBO_STOCK.stage_unknown, FBO_STOCK.stage_cancelled}
        and bool(fulfillment)
        and fulfillment in evidence.fulfillments
        and (not sources or sources == {fulfillment})
        and valid_items
        and not shortages
        and current.issue not in {FboStockIssue.LEGACY, FboStockIssue.CANCELLED, FboStockIssue.UNCONFIRMED}
        and is_active(target, current.supply)
        and (not current.posted_at or not allocations_match(current))
    )
    allocations = tuple(
        row.model_copy(
            update={
                "remaining_quantity": max(row.quantity - row.received_quantity, 0)
                if current.state == FboStockState.ACTIVE
                else 0,
            }
        )
        for row in current.allocations
    )
    return current.model_copy(
        update={
            "allocations": allocations,
            "remaining_quantity": sum(row.remaining_quantity for row in allocations),
            "shortages": shortages,
            "stock_checked": valid_items and bool(fulfillment or current.checked_fulfillment),
            "can_post": can_post,
            "can_select_fulfillment": can_select_fulfillment(target, current),
            "processed": bool(fulfillment or current.allocations),
        }
    )
