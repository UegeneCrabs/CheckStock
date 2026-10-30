from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from app.application.ports import OzonCabinetUnitOfWork
from app.core.constants import OZON_CABINET, TIME, OzonBuyoutError, OzonBuyoutState
from app.dto.ozon_buyout import OzonBuyoutEvidence
from app.dto.ozon_cabinet import OzonBuyoutSyncResult, OzonCabinetRecord, OzonCabinetSave, OzonCabinetUpdate
from app.dto.ozon_economics import OzonProductsQuery
from app.dto.ozon_promotions import OzonBoostRecord, OzonBoostSave, OzonBoostUpdate
from app.ozon.cabinet_normalizer import buyout_snapshot


class OzonCabinetService:
    def __init__(
        self,
        repository: OzonCabinetUnitOfWork,
        load_orders: Callable[[OzonProductsQuery], Awaitable[OzonBuyoutEvidence]],
        articles: Callable[[str], Awaitable[tuple[str, ...]]],
        clock: Callable[[], datetime],
    ):
        self.repository = repository
        self.load_orders = load_orders
        self.articles = articles
        self.clock = clock

    async def save(self, store: str, change: OzonCabinetUpdate, user_name: str) -> OzonCabinetRecord:
        return await self.repository.save(
            OzonCabinetSave(
                store_slug=store,
                change=change,
                user_name=user_name,
                saved_at=self.clock(),
            )
        )

    async def save_boost(self, change: OzonBoostUpdate, user_name: str) -> OzonBoostRecord:
        return await self.repository.save_boost(
            OzonBoostSave(change=change, user_name=user_name, saved_at=self.clock())
        )

    async def sync_buyout(self, store: str) -> OzonBuyoutSyncResult:
        try:
            return await self._sync_buyout(store)
        except Exception as error:
            await self.repository.fail_buyout(store, type(error).__name__)
            raise

    async def _sync_buyout(self, store: str) -> OzonBuyoutSyncResult:
        today = self.clock().astimezone(TIME.moscow_timezone).date()
        data = await self.load_orders(
            OzonProductsQuery(
                stores=(store,),
                period_from=today - timedelta(days=OZON_CABINET.default_period_days),
                period_to=today - timedelta(days=1),
            )
        )
        now = self.clock()
        if now.astimezone(TIME.moscow_timezone).date() != today:
            raise ValueError("Buyout acquisition crossed Moscow midnight")
        snapshot = buyout_snapshot(store, await self.articles(store), data, now)
        await self.repository.save_buyout(snapshot)
        return OzonBuyoutSyncResult(
            stores=1,
            products=len(snapshot.products),
            error=OzonBuyoutError.INCOMPLETE_COVERAGE if snapshot.state == OzonBuyoutState.PARTIAL else None,
        )
