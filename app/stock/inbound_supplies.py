from collections.abc import Callable
from pathlib import Path

from app.application.inbound_supplies import InboundNotConfiguredError, InboundSupplyService, Target
from app.core.stores import STORES
from app.dto.inbound_supplies import InboundSupply
from app.infrastructure.database import database_for_path
from app.infrastructure.inbound_repository import SqlAlchemyInboundRepository
from app.ozon import api as ozon_api
from app.ozon import inbound as ozon_inbound
from app.ozon import tokens as ozon_tokens
from app.repositories import core
from app.stock.inbound_common import api_error
from app.wb import api as wb_api
from app.wb import inbound as wb_inbound
from app.wb import tokens as wb_tokens
from app.yandex import api as yandex_api
from app.yandex import inbound as yandex_inbound
from app.yandex import tokens as yandex_tokens
from app.yandex.sync import resolve_campaigns

MARKETPLACES = ("WB", "OZON", "YANDEX MARKET")
JOB_NAME = "inbound_supplies_sync"


def load_target(target: Target, previous: tuple[InboundSupply, ...]) -> tuple[InboundSupply, ...]:
    store, marketplace = target
    if store not in STORES or marketplace not in MARKETPLACES:
        raise ValueError("Неизвестный магазин или маркетплейс")
    try:
        if marketplace == "WB":
            if not wb_tokens.has_token(store):
                raise InboundNotConfiguredError("Для этого магазина не добавлен ключ WB.")
            return wb_inbound.load(wb_tokens.get_token(store), previous)
        if marketplace == "OZON":
            if not ozon_tokens.has_credentials(store):
                raise InboundNotConfiguredError("Для этого магазина не добавлены доступы Ozon.")
            client_id, key = ozon_tokens.get_credentials(store)
            return ozon_inbound.load(client_id, key, previous)
        if not yandex_tokens.has_credentials(store):
            raise InboundNotConfiguredError("Для этого магазина не добавлен ключ Яндекс Маркета.")
        key = yandex_tokens.get_api_key(store)
        campaigns = resolve_campaigns(store, key)
        if not any(campaign.get("scheme") in {"fby", "fbo"} for campaign in campaigns):
            raise InboundNotConfiguredError("У этого магазина не найдено подключение FBY.")
        return yandex_inbound.load(key, campaigns, previous)
    except (wb_api.WBApiError, ozon_api.OzonApiError, yandex_api.YandexApiError) as error:
        raise api_error(marketplace, error) from error


def build_service(database_path: Callable[[], Path] | None = None) -> InboundSupplyService:
    path = database_path or (lambda: core.DB_PATH)
    repository = SqlAlchemyInboundRepository(lambda: database_for_path(path()).session_factory())
    return InboundSupplyService(repository, load_target)


def sync_all() -> dict:
    from app.jobs import settings as sync_settings

    targets = tuple(
        (store, marketplace)
        for marketplace in MARKETPLACES
        for store in sync_settings.enabled_stores(JOB_NAME, marketplace)
    )
    # The scheduler and integration controls call this under run_tracked's lock.
    return build_service().sync(targets, recover_interrupted=True)
