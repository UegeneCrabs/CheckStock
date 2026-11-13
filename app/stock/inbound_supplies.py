import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path

from app.application.inbound_supplies import (
    AsyncInboundSupplyService,
    InboundNotConfiguredError,
    InboundSupplyService,
    SyncResult,
    Target,
)
from app.core.constants import JOBS, Marketplace
from app.core.stores import STORES
from app.dto.fbo_stock import FboTarget
from app.dto.inbound_supplies import InboundMarketplace, InboundSupply
from app.infrastructure.async_database import AsyncDatabase
from app.infrastructure.async_http import store_context
from app.infrastructure.async_resources import AsyncResources
from app.infrastructure.database import database_for_path
from app.infrastructure.inbound_repository import (
    AsyncSqlAlchemyInboundRepository,
    SqlAlchemyInboundRepository,
)
from app.ozon import api as ozon_api
from app.ozon import inbound as ozon_inbound
from app.ozon import tokens as ozon_tokens
from app.repositories import core
from app.stock.fbo_sync import reconcile_target
from app.stock.inbound_common import api_error
from app.wb import api as wb_api
from app.wb import inbound as wb_inbound
from app.wb import tokens as wb_tokens
from app.yandex import api as yandex_api
from app.yandex import inbound as yandex_inbound
from app.yandex import tokens as yandex_tokens
from app.yandex.sync import resolve_campaigns

MARKETPLACES: tuple[InboundMarketplace, ...] = ("WB", "OZON", "YANDEX MARKET")
JOB_NAME = JOBS.inbound_supplies.name


def load_target(target: Target, previous: tuple[InboundSupply, ...]) -> tuple[InboundSupply, ...]:
    store, marketplace = target
    if store not in STORES or marketplace not in MARKETPLACES:
        raise ValueError("Неизвестный магазин или маркетплейс")
    try:
        if marketplace == "WB":
            if not wb_tokens.has_token(store):
                raise InboundNotConfiguredError("Для этого магазина не добавлен ключ WB.")
            return wb_inbound.load(wb_tokens.get_token(store), previous)
        if marketplace == Marketplace.OZON:
            raise ValueError("Для поставок Ozon требуется асинхронный загрузчик")
        if not yandex_tokens.has_credentials(store):
            raise InboundNotConfiguredError("Для этого магазина не добавлен ключ Яндекс Маркета.")
        key = yandex_tokens.get_api_key(store)
        campaigns = resolve_campaigns(store, key)
        if not any(campaign.get("scheme") in {"fby", "fbo"} for campaign in campaigns):
            raise InboundNotConfiguredError("У этого магазина не найдено подключение FBY.")
        return yandex_inbound.load(key, campaigns, previous)
    except (wb_api.WBApiError, ozon_api.OzonApiError, yandex_api.YandexApiError) as error:
        raise api_error(marketplace, error) from error


async def sync_ozon(targets: Sequence[Target], path: Path, *, recover_interrupted: bool) -> SyncResult:
    from app.application.fbo_stock import FboStockService
    from app.infrastructure.fbo_stock_repository import FboStockRepository

    async with AsyncResources(database=AsyncDatabase(path=path)) as resources:

        async def load(target: Target, previous: tuple[InboundSupply, ...]) -> tuple[InboundSupply, ...]:
            context = store_context.set(target[0])
            try:
                credentials = ozon_tokens.get_order_credentials(target[0])
                return await ozon_inbound.AsyncOzonInbound(resources.http, credentials).load(previous)
            except ozon_tokens.OzonCredentialsNotFoundError:
                raise InboundNotConfiguredError("Для этого магазина не добавлены доступы Ozon.") from None
            except ozon_api.OzonApiError as error:
                raise api_error(target[1], error) from error
            finally:
                store_context.reset(context)

        async def reconcile(target: Target) -> None:
            await FboStockService(FboStockRepository(resources.database)).sync(
                FboTarget(store_slug=target[0], marketplace=target[1])
            )

        service = AsyncInboundSupplyService(
            AsyncSqlAlchemyInboundRepository(resources.database), load, reconcile
        )
        return await service.sync(targets, recover_interrupted=recover_interrupted)


def build_service(database_path: Callable[[], Path] | None = None) -> InboundSupplyService:
    path = database_path or (lambda: core.DB_PATH)
    repository = SqlAlchemyInboundRepository(lambda: database_for_path(path()).session_factory())
    return InboundSupplyService(
        repository,
        load_target,
        after_sync=lambda target: reconcile_target(
            FboTarget(store_slug=target[0], marketplace=target[1]),
            path(),
        ),
        ozon_sync=lambda targets, *, recover_interrupted: asyncio.run(
            sync_ozon(targets, path(), recover_interrupted=recover_interrupted)
        ),
    )


def sync_all() -> SyncResult:
    from app.jobs import settings as sync_settings

    targets = tuple(
        (store, marketplace)
        for marketplace in MARKETPLACES
        for store in sync_settings.enabled_stores(JOB_NAME, marketplace)
    )
    # The scheduler and integration controls call this under run_tracked's lock.
    return build_service().sync(targets, recover_interrupted=True)
