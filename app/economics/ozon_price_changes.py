from datetime import datetime

from app.core.constants import (
    OZON_PRICE_CHANGES,
    OZON_PRICES,
    OzonPriceCalculatorField,
    OzonPriceChangeIssue,
    OzonPriceSource,
    OzonPromotionStatus,
)
from app.dto.ozon_price_calculator import OzonPriceCalculation, OzonPriceCalculationRequest, OzonPriceEdit
from app.dto.ozon_price_changes import (
    OzonPriceChangeEvidence,
    OzonPriceChangeImpact,
    OzonPriceChangePlan,
    OzonPriceChangeRequest,
)
from app.dto.ozon_prices import PriceIdentity, PriceObservation, PriceValues, WebsiteEvidence
from app.dto.ozon_promotions import OzonPromotionItem
from app.economics.ozon_price_calculator import calculate
from app.ozon.price_normalizer import seller_observation, website_observation


def observations(evidence: OzonPriceChangeEvidence) -> tuple[PriceObservation, ...]:
    now = evidence.received_at
    websites = tuple(
        WebsiteEvidence(
            product_id=evidence.identity.id,
            action_id=item.action.id,
            source=OzonPriceSource.PRODUCTS
            if item.status == OzonPromotionStatus.PARTICIPATING
            else OzonPriceSource.CANDIDATES,
            received_at=now,
            prices=item.product.website_prices.prices_by_schema[scheme.value.upper()],
            scheme=scheme,
        )
        for item in evidence.promotions.items
        if item.product and item.product.website_prices
        for scheme in OZON_PRICES.website_schemes
        if scheme.value.upper() in item.product.website_prices.prices_by_schema
    )
    return (
        seller_observation(evidence.prices, now),
        website_observation(
            PriceIdentity(product_id=evidence.identity.id, offer_id=evidence.identity.offer_id), websites, now
        ),
    )


def price_values(evidence: OzonPriceChangeEvidence) -> PriceValues:
    return PriceValues.model_validate(
        {change.field.value: change.value for item in observations(evidence) for change in item.changes}
    )


def conditions(evidence: OzonPriceChangeEvidence) -> tuple[OzonPromotionItem, ...]:
    return tuple(
        item.model_copy(
            update={
                "product": item.product.model_copy(
                    update={"website_prices": None, "price": None, "action_price": None}
                )
                if item.product
                else None
            }
        )
        for item in sorted(evidence.promotions.items, key=lambda item: item.action.id)
    )


def changed(before: OzonPriceChangeEvidence, after: OzonPriceChangeEvidence) -> bool:
    return (
        before.identity.id != after.identity.id
        or before.identity.offer_id != after.identity.offer_id
        or before.prices.price != after.prices.price
        or conditions(before) != conditions(after)
    )


def plan(request: OzonPriceChangeRequest, evidence: OzonPriceChangeEvidence) -> OzonPriceChangePlan | None:
    prices = evidence.prices.price
    if (
        prices.price is None
        or prices.old_price is None
        or prices.min_price is None
        or prices.currency_code is None
    ):
        return None
    values = price_values(evidence)
    prediction = calculate(
        OzonPriceCalculationRequest(
            values=OzonPriceCalculation(
                base_price=values.base_price.amount,
                seller_price=values.seller_price.amount,
                black_price=values.black_price.amount,
                green_price=values.green_price.amount,
            ),
            initialize=True,
            edits=(
                OzonPriceEdit(
                    field=OzonPriceCalculatorField(request.target_field.value), value=request.target_price
                ),
            ),
        )
    )
    if prediction.base_price is None or prediction.base_price <= 0:
        return None
    impacts: list[OzonPriceChangeImpact] = []
    for item in evidence.promotions.items:
        limits = (
            tuple(
                value.amount
                for value in (item.product.max_action_price, item.product.price_min_elastic)
                if value is not None
            )
            if item.product
            else ()
        )
        impacts.append(
            OzonPriceChangeImpact(
                action_id=item.action.id,
                title=item.action.title,
                current_status=item.status,
                expected_participation=prediction.base_price <= min(limits)
                if limits and item.action.is_voucher_action is False
                else None,
            )
        )
    return OzonPriceChangePlan(
        price=prediction.base_price,
        previous_price=prices.price,
        old_price=prices.old_price,
        min_price=prices.min_price,
        currency=prices.currency_code,
        prediction=prediction,
        impacts=tuple(impacts),
        previous_prices=values,
    )


def issue(
    evidence: OzonPriceChangeEvidence, prepared: OzonPriceChangePlan | None, now: datetime
) -> OzonPriceChangeIssue | None:
    if evidence.identity.is_archived or evidence.identity.is_autoarchived:
        return OzonPriceChangeIssue.QUARANTINED
    if evidence.identity.is_archived is None or evidence.identity.is_autoarchived is None:
        return OzonPriceChangeIssue.INCOMPLETE
    if not evidence.promotions.complete:
        return OzonPriceChangeIssue.INCOMPLETE
    if prepared is None:
        return OzonPriceChangeIssue.PRICE_UNAVAILABLE
    if prepared.currency != OZON_PRICE_CHANGES.currency:
        return OzonPriceChangeIssue.CURRENCY
    if prepared.price < prepared.min_price:
        return OzonPriceChangeIssue.MIN_PRICE
    if prepared.old_price and prepared.price >= prepared.old_price:
        return OzonPriceChangeIssue.OLD_PRICE
    for item in evidence.promotions.items:
        if item.product and item.product.is_quarantined:
            return OzonPriceChangeIssue.QUARANTINED
        if (
            item.status == OzonPromotionStatus.PARTICIPATING
            and item.action.freeze_date
            and item.action.freeze_date <= now
            and prepared.price > prepared.previous_price
        ):
            return OzonPriceChangeIssue.FROZEN
    return None
