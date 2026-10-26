from collections.abc import Awaitable, Callable
from datetime import datetime

from app.application.ports import LogisticsUnitOfWork
from app.dto.ozon_logistics import LogisticsEvidence, LogisticsPeriod, LogisticsSyncResult, moscow_day
from app.ozon.logistics_normalizer import normalize


class OzonLogisticsService:
    def __init__(
        self,
        store: str,
        unit_of_work: LogisticsUnitOfWork,
        evidence: Callable[[LogisticsPeriod, tuple[str, ...]], Awaitable[LogisticsEvidence]],
        clock: Callable[[], datetime],
    ):
        self.store = store
        self.unit_of_work = unit_of_work
        self.evidence = evidence
        self.clock = clock

    async def sync(self) -> LogisticsSyncResult:
        period = LogisticsPeriod.for_day(moscow_day(self.clock()))
        if await self.unit_of_work.completed(self.store, period.day):
            return LogisticsSyncResult(stores=1, skipped=1)
        try:
            articles = await self.unit_of_work.articles(self.store)
            evidence = await self.evidence(period, articles)
            now = self.clock()
            if moscow_day(now) != period.day:
                raise ValueError("Logistics acquisition crossed Moscow midnight")
            snapshot = normalize(self.store, evidence, articles, now)
            await self.unit_of_work.commit(snapshot)
            return LogisticsSyncResult(stores=1, products=len(snapshot.products))
        except Exception as error:
            await self.unit_of_work.fail(self.store, period.day, type(error).__name__)
            raise
