import asyncio
import logging
from datetime import UTC, datetime

from app.core.constants import OZON_PRICES, OzonPriceSource, OzonPriceState
from app.dto.ozon_commissions import FboCommissionObservation, FboCommissionValue
from app.dto.ozon_prices import (
    PriceChange,
    PriceIdentity,
    PriceObservation,
    PriceSyncResult,
    PriceValue,
    WebsiteEvidence,
)
from app.infrastructure.async_resources import AsyncResources
from app.jobs import async_locks
from app.ozon import tokens
from app.ozon.async_prices import AsyncPricesClient
from app.ozon.price_normalizer import (
    fbo_commission_observation,
    seller_observation,
    website_observation,
    website_value,
)

logger = logging.getLogger(__name__)


async def website_observations(
    client: AsyncPricesClient, identities: tuple[PriceIdentity, ...]
) -> tuple[PriceObservation, ...]:
    actions = await client.action_ids()
    evidence: dict[int, list[WebsiteEvidence]] = {item.product_id: [] for item in identities}
    candidates = set(evidence)
    for source in (OzonPriceSource.PRODUCTS, OzonPriceSource.CANDIDATES):
        if not candidates:
            break
        for action_id in actions:
            async for page in client.action_pages(action_id, source):
                for product in page.products:
                    if product.id not in candidates or product.website_prices is None:
                        continue
                    prices = product.website_prices.prices_by_schema.get(OZON_PRICES.scheme)
                    if prices is not None:
                        evidence[product.id].append(
                            WebsiteEvidence(
                                product_id=product.id,
                                action_id=action_id,
                                source=source,
                                received_at=page.received_at,
                                prices=prices,
                            )
                        )
        if source == OzonPriceSource.PRODUCTS:
            checked = datetime.now(UTC)
            candidates = {
                product_id
                for product_id, items in evidence.items()
                if any(
                    website_value(tuple(items), field, checked).state == OzonPriceState.MISSING
                    for field in OZON_PRICES.website_fields
                )
            }
    checked = datetime.now(UTC)
    return tuple(
        website_observation(identity, tuple(evidence[identity.product_id]), checked)
        for identity in identities
    )


async def sync_store(store: str, resources: AsyncResources) -> PriceSyncResult:
    async with async_locks.hold(OZON_PRICES.lock_prefix + store, resources.database):
        repository = resources.prices
        closed = await repository.close_days(store, datetime.now(UTC))
        closed_commissions = await resources.commissions.close_days(store, datetime.now(UTC))
        commission_count = 0
        identities = {item.product_id: item for item in await repository.catalog(store)}
        client = AsyncPricesClient(resources.http, tokens.get_order_credentials(store))
        errors: list[str] = []
        seen: set[int] = set()
        try:
            async for page in client.seller_pages():
                observations: list[PriceObservation] = []
                for item in page.items:
                    if item.product_id in seen:
                        raise ValueError("Ozon seller prices repeated product_id")
                    seen.add(item.product_id)
                    identity = PriceIdentity(product_id=item.product_id, offer_id=item.offer_id)
                    identities[item.product_id] = identity
                    observations.append(seller_observation(item, page.received_at))
                await repository.save(store, tuple(observations))
                commission_count += await resources.commissions.save(
                    store, tuple(fbo_commission_observation(item, page.received_at) for item in page.items)
                )
            checked = datetime.now(UTC)
            await repository.save(
                store,
                tuple(
                    PriceObservation(
                        **identity.model_dump(),
                        changes=tuple(
                            PriceChange(
                                field=field,
                                value=PriceValue(
                                    received_at=checked,
                                    attempted_at=checked,
                                    source=OzonPriceSource.SELLER,
                                ),
                            )
                            for field in OZON_PRICES.seller_fields
                        ),
                    )
                    for product_id, identity in identities.items()
                    if product_id not in seen
                ),
            )
            await resources.commissions.save(
                store,
                tuple(
                    FboCommissionObservation(
                        **identity.model_dump(),
                        value=FboCommissionValue(received_at=checked, attempted_at=checked),
                    )
                    for product_id, identity in identities.items()
                    if product_id not in seen
                ),
            )
        except Exception as error:
            logger.warning("ozon_prices_seller_failed store=%s error=%s", store, type(error).__name__)
            await repository.fail(
                store, tuple(identities.values()), OZON_PRICES.seller_fields, datetime.now(UTC)
            )
            await resources.commissions.fail(store, tuple(identities.values()), datetime.now(UTC))
            errors.append("seller_prices_failed")
        try:
            website = await website_observations(client, tuple(identities.values()))
            await repository.save(store, website)
            if any(
                change.value.state == OzonPriceState.CONFLICT for item in website for change in item.changes
            ):
                errors.append("website_prices_conflict")
        except Exception as error:
            logger.warning("ozon_prices_website_failed store=%s error=%s", store, type(error).__name__)
            await repository.fail(
                store, tuple(identities.values()), OZON_PRICES.website_fields, datetime.now(UTC)
            )
            errors.append("website_prices_failed")
        closed += await repository.close_days(store, datetime.now(UTC))
        closed_commissions += await resources.commissions.close_days(store, datetime.now(UTC))
        logger.info(
            "ozon_prices_synced store=%s products=%s closed=%s errors=%s",
            store,
            len(identities),
            closed,
            errors,
        )
        return PriceSyncResult(
            stores=1,
            products=len(identities),
            closed_days=closed,
            fbo_commissions=commission_count,
            closed_commission_days=closed_commissions,
            error=f"{store}: {', '.join(errors)}" if errors else None,
        )


async def sync_all(stores: tuple[str, ...], *, resources: AsyncResources) -> PriceSyncResult:
    results: list[PriceSyncResult] = []

    async def run(store: str) -> None:
        try:
            results.append(await sync_store(store, resources))
        except Exception as error:
            logger.warning("ozon_prices_store_failed store=%s error=%s", store, type(error).__name__)
            results.append(PriceSyncResult(stores=1, error=f"{store}: {type(error).__name__}"))

    async with asyncio.TaskGroup() as group:
        for store in stores:
            if tokens.has_credentials(store):
                group.create_task(run(store))
    return PriceSyncResult(
        stores=sum(item.stores for item in results),
        products=sum(item.products for item in results),
        closed_days=sum(item.closed_days for item in results),
        fbo_commissions=sum(item.fbo_commissions for item in results),
        closed_commission_days=sum(item.closed_commission_days for item in results),
        error="; ".join(item.error for item in results if item.error) or None,
    )


async def close_days(resources: AsyncResources) -> PriceSyncResult:
    results: list[PriceSyncResult] = []

    async def close(store: str) -> None:
        try:
            async with async_locks.hold(OZON_PRICES.lock_prefix + store, resources.database):
                count = await resources.prices.close_days(store, datetime.now(UTC))
                commissions = await resources.commissions.close_days(store, datetime.now(UTC))
                results.append(
                    PriceSyncResult(stores=1, closed_days=count, closed_commission_days=commissions)
                )
        except Exception as error:
            results.append(PriceSyncResult(error=f"{store}: {type(error).__name__}"))

    pending = set(await resources.prices.pending_stores(datetime.now(UTC)))
    pending.update(await resources.commissions.pending_stores(datetime.now(UTC)))
    async with asyncio.TaskGroup() as group:
        for store in sorted(pending):
            group.create_task(close(store))
    return PriceSyncResult(
        stores=sum(item.stores for item in results),
        closed_days=sum(item.closed_days for item in results),
        closed_commission_days=sum(item.closed_commission_days for item in results),
        error="; ".join(item.error for item in results if item.error) or None,
    )
