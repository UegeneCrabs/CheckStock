from datetime import datetime

from app.core.constants import (
    OZON_PRICES,
    OzonCommissionState,
    OzonPriceField,
    OzonPriceSource,
    OzonPriceState,
)
from app.dto.ozon_commissions import FboCommissionObservation, FboCommissionValue
from app.dto.ozon_prices import (
    PriceChange,
    PriceIdentity,
    PriceObservation,
    PriceValue,
    PriceValues,
    SellerPriceItem,
    WebsiteAmount,
    WebsiteEvidence,
)


def seller_observation(item: SellerPriceItem, received_at: datetime) -> PriceObservation:
    amounts = {
        OzonPriceField.BASE: item.price.price,
        OzonPriceField.SELLER: item.price.marketing_seller_price,
    }
    currency = item.price.currency_code
    if any(value is not None for value in amounts.values()) and not currency:
        raise ValueError("Ozon seller prices have no currency")
    return PriceObservation(
        product_id=item.product_id,
        offer_id=item.offer_id,
        changes=tuple(
            PriceChange(
                field=field,
                value=PriceValue(
                    amount=amount,
                    currency=currency,
                    received_at=received_at,
                    attempted_at=received_at,
                    source=OzonPriceSource.SELLER,
                    state=OzonPriceState.AVAILABLE if amount is not None else OzonPriceState.MISSING,
                ),
            )
            for field, amount in amounts.items()
        ),
    )


def fbo_commission_observation(item: SellerPriceItem, received_at: datetime) -> FboCommissionObservation:
    percent = item.commissions.sales_percent_fbo
    return FboCommissionObservation(
        product_id=item.product_id,
        offer_id=item.offer_id,
        value=FboCommissionValue(
            percent=percent,
            received_at=received_at,
            attempted_at=received_at,
            state=OzonCommissionState.AVAILABLE if percent is not None else OzonCommissionState.MISSING,
        ),
    )


def website_amount(evidence: WebsiteEvidence, field: OzonPriceField) -> WebsiteAmount | None:
    return evidence.prices.black_price if field == OzonPriceField.BLACK else evidence.prices.green_price


def website_value(
    evidence: tuple[WebsiteEvidence, ...], field: OzonPriceField, checked_at: datetime
) -> PriceValue:
    available = tuple(
        item
        for item in evidence
        if (amount := website_amount(item, field)) is not None and amount.amount is not None
    )
    participants = tuple(item for item in available if item.source == OzonPriceSource.PRODUCTS)
    selected = participants or available
    if not selected:
        return PriceValue(received_at=checked_at, attempted_at=checked_at)
    values = {website_amount(item, field) for item in selected}
    if any(item is not None and not item.currency for item in values):
        raise ValueError("Ozon website prices have no currency")
    newest = max(selected, key=lambda item: item.received_at)
    value = website_amount(newest, field)
    conflict = len(values) != 1
    return PriceValue(
        amount=value.amount if value is not None and not conflict else None,
        currency=value.currency if value is not None and not conflict else None,
        received_at=newest.received_at,
        attempted_at=checked_at,
        source=newest.source,
        action_ids=tuple(sorted({item.action_id for item in selected})),
        state=OzonPriceState.CONFLICT if conflict else OzonPriceState.AVAILABLE,
    )


def website_observation(
    identity: PriceIdentity, evidence: tuple[WebsiteEvidence, ...], checked_at: datetime
) -> PriceObservation:
    return PriceObservation(
        **identity.model_dump(),
        changes=tuple(
            PriceChange(field=field, value=website_value(evidence, field, checked_at))
            for field in OZON_PRICES.website_fields
        ),
    )


def price_freshness(prices: PriceValues, now: datetime) -> PriceValues:
    values: dict[str, PriceValue] = {}
    for field in OzonPriceField:
        value = prices.value(field)
        received = value.received_at
        stale = received is not None and (
            (now - received).total_seconds() > OZON_PRICES.stale_seconds
            or value.state == OzonPriceState.ERROR
        )
        values[field.value] = value.model_copy(update={"stale": stale})
    return PriceValues.model_validate(values)
