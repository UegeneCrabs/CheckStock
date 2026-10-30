from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.core.constants import OZON_LOGISTICS, OzonLogisticsPart, OzonLogisticsState
from app.dto.ozon_logistics import (
    LogisticsCalculation,
    LogisticsCharge,
    LogisticsEvidence,
    LogisticsInputs,
    LogisticsScenario,
    LogisticsSnapshot,
    LogisticsStoreSnapshot,
    moscow_day,
)


def modeled_cost(values: LogisticsInputs | LogisticsScenario) -> Decimal | None:
    percent = values.buyout_percent
    if (
        percent is None
        or percent <= 0
        or percent > 100
        or values.forward is None
        or values.last_mile is None
        or values.extra is None
    ):
        return None
    share = percent / 100
    reverse_cost = Decimal(0)
    if share < 1:
        if values.reverse is None or values.handling is None:
            return None
        reverse_cost = (values.reverse + values.handling) * (1 - share) / share
    return (values.forward + values.markup) / share + reverse_cost + values.last_mile + values.extra


def calculate(values: LogisticsScenario) -> LogisticsCalculation:
    modeled = modeled_cost(values)
    return LogisticsCalculation(
        total=values.total if values.total is not None else modeled,
        modeled=modeled,
        manual=values.total is not None,
    )


@dataclass(slots=True, kw_only=True)
class ChargedItem:
    article: str
    quantity: int
    status: str
    returned: int = 0
    amounts: dict[int, Decimal] = field(default_factory=dict)
    quantities: dict[int, int] = field(default_factory=dict)


def normalize(
    store: str, evidence: LogisticsEvidence, articles: tuple[str, ...], now: datetime
) -> LogisticsStoreSnapshot:
    types = {item.id: item.name for item in evidence.types}
    if len(types) != len(evidence.types):
        raise ValueError("Duplicate Ozon accrual type")
    parts = dict(OZON_LOGISTICS.accrual_parts)
    if not {name for name, part in OZON_LOGISTICS.accrual_parts if part != OzonLogisticsPart.EXTRA} <= set(
        types.values()
    ):
        raise ValueError("Missing Ozon logistics accrual types")
    items: dict[tuple[str, str], ChargedItem] = {}
    for posting in evidence.postings:
        if not posting.products:
            raise ValueError("Ozon posting has no products")
        for product in posting.products:
            key = (posting.posting_number, product.sku)
            if not product.offer_id or not product.sku or key in items:
                raise ValueError("Missing or ambiguous posting product")
            items[key] = ChargedItem(
                article=product.offer_id, quantity=product.quantity, status=posting.status
            )
    for posting_accruals in evidence.accruals:
        for accrual in posting_accruals.accruals:
            if not evidence.period.start <= accrual.accrual_date <= evidence.period.end:
                continue
            if accrual.type_id not in types:
                raise ValueError("Unknown Ozon accrual type")
            if types[accrual.type_id] not in parts:
                continue
            matched = (
                [items[(posting_accruals.posting_number, accrual.sku)]]
                if (posting_accruals.posting_number, accrual.sku) in items
                else []
            )
            if not accrual.sku:
                matched = [
                    item
                    for (posting_number, _), item in items.items()
                    if posting_number == posting_accruals.posting_number
                ]
                if parts[types[accrual.type_id]] != OzonLogisticsPart.EXTRA and len(matched) != 1:
                    raise ValueError("Unallocated logistics service shared by multiple SKUs")
            if not matched or accrual.accrued.currency != OZON_LOGISTICS.currency:
                raise ValueError("Unallocated or incompatible logistics accrual")
            total_quantity = sum(item.quantity for item in matched)
            for item in matched:
                count = accrual.quantity if accrual.sku and accrual.quantity is not None else item.quantity
                if count > item.quantity:
                    raise ValueError("Accrual quantity exceeds posting quantity")
                amount = accrual.accrued.amount * item.quantity / total_quantity
                item.amounts[accrual.type_id] = item.amounts.get(accrual.type_id, Decimal(0)) - amount
                if amount < 0:
                    item.quantities[accrual.type_id] = max(item.quantities.get(accrual.type_id, 0), count)
    for returned in evidence.returns:
        if returned.type != OZON_LOGISTICS.client_return_type:
            continue
        moment = returned.logistic.return_date
        if moment is None or moscow_day(moment) > evidence.period.end:
            continue
        return_item = items.get((returned.posting_number, returned.product.sku))
        if return_item is None:
            raise ValueError("Unallocated customer return")
        return_item.returned += returned.product.quantity
        if return_item.returned > return_item.quantity:
            raise ValueError("Customer returns exceed posting quantity")
    by_article: dict[str, list[ChargedItem]] = {article: [] for article in articles}
    for item in items.values():
        by_article.setdefault(item.article, []).append(item)
    volumes = {item.offer_id: item.volume() for item in evidence.dimensions}
    products: list[LogisticsSnapshot] = []
    for article, lines in sorted(by_article.items()):
        totals = {part: Decimal(0) for part in OzonLogisticsPart}
        counts = {part: 0 for part in OzonLogisticsPart}
        charge_amounts: dict[int, Decimal] = {}
        charge_units: dict[int, int] = {}
        paid = received = returned_units = cancelled = pending = 0
        issues: set[str] = set()
        for line in lines:
            has_expense = any(value != 0 for value in line.amounts.values())
            line_counts = {part: 0 for part in OzonLogisticsPart}
            for type_id, amount in line.amounts.items():
                part = parts[types[type_id]]
                totals[part] += amount
                charge_amounts[type_id] = charge_amounts.get(type_id, Decimal(0)) + amount
                count = line.quantities.get(type_id, 0) if amount > 0 else 0
                charge_units[type_id] = charge_units.get(type_id, 0) + count
                line_counts[part] = max(line_counts[part], count)
            for part in OzonLogisticsPart:
                counts[part] += line_counts[part]
            if not has_expense:
                continue
            paid += line.quantity
            if line.status == OZON_LOGISTICS.delivered_status:
                received += line.quantity
                returned_units += line.returned
            elif line.status == OZON_LOGISTICS.cancelled_status:
                cancelled += line.quantity
            else:
                pending += line.quantity
        retained = received - returned_units
        forward_units = counts[OzonLogisticsPart.FORWARD]
        reverse_units = counts[OzonLogisticsPart.REVERSE]
        ordered = sum(line.quantity for line in lines)
        total = sum(totals.values(), Decimal(0))
        if pending:
            issues.add("pending_paid_postings")
        if not evidence.dimensions_available or volumes.get(article) is None:
            issues.add("volume_unavailable")
        inputs = LogisticsInputs(
            forward=totals[OzonLogisticsPart.FORWARD] / forward_units if forward_units else None,
            reverse=totals[OzonLogisticsPart.REVERSE] / reverse_units if reverse_units else None,
            handling=totals[OzonLogisticsPart.HANDLING] / reverse_units if reverse_units else None,
            last_mile=totals[OzonLogisticsPart.LAST_MILE] / retained if retained else None,
            extra=totals[OzonLogisticsPart.EXTRA] / retained if retained else None,
            buyout_percent=Decimal(retained) / paid * 100 if paid else None,
        )
        state = OzonLogisticsState.AVAILABLE
        if not retained:
            state = OzonLogisticsState.INSUFFICIENT
        elif issues - {"volume_unavailable"}:
            state = OzonLogisticsState.PARTIAL
        products.append(
            LogisticsSnapshot(
                store_slug=store,
                article=article,
                period=evidence.period,
                observed_at=now,
                state=state,
                inputs=inputs,
                actual_per_unit=total / retained if retained else None,
                modeled_per_unit=modeled_cost(inputs) if paid == forward_units else None,
                total_expenses=total,
                volume_l=volumes.get(article),
                ordered_units=ordered,
                paid_units=paid,
                forward_units=forward_units,
                reverse_units=reverse_units,
                handling_units=counts[OzonLogisticsPart.HANDLING],
                received_units=received,
                returned_units=returned_units,
                retained_units=retained,
                cancelled_units=cancelled,
                pending_paid_units=pending,
                unpaid_units=ordered - paid,
                charges=tuple(
                    LogisticsCharge(
                        type_id=type_id,
                        name=types[type_id],
                        part=parts[types[type_id]],
                        amount=amount,
                        charged_units=charge_units[type_id],
                    )
                    for type_id, amount in sorted(charge_amounts.items())
                ),
                issues=tuple(sorted(issues)),
            )
        )
    return LogisticsStoreSnapshot(
        store_slug=store, period=evidence.period, observed_at=now, products=tuple(products)
    )
