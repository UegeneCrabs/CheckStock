from collections.abc import Callable
from datetime import datetime

from app.application.ports import StorageSource, StorageUnitOfWork
from app.core.constants import OZON_STORAGE
from app.dto.ozon_logistics import moscow_day
from app.dto.ozon_storage import StoragePeriod, StorageReportTicket, StorageSyncResult
from app.ozon.async_storage import StorageReportFailedError
from app.ozon.storage_normalizer import normalize


class OzonStorageService:
    def __init__(
        self,
        store: str,
        unit_of_work: StorageUnitOfWork,
        source: StorageSource,
        clock: Callable[[], datetime],
    ):
        self.store = store
        self.unit_of_work = unit_of_work
        self.source = source
        self.clock = clock

    async def sync(self) -> StorageSyncResult:
        period = StoragePeriod.for_day(moscow_day(self.clock()))
        if await self.unit_of_work.completed(self.store, period.day):
            return StorageSyncResult(stores=1, skipped=1)
        try:
            ticket = await self.unit_of_work.ticket(self.store, period.day)
            if not ticket.code:
                if ticket.attempts >= OZON_STORAGE.max_report_attempts:
                    raise RuntimeError("Storage report daily retry limit reached")
                ticket = StorageReportTicket(attempts=ticket.attempts + 1)
                await self.unit_of_work.save_ticket(self.store, period.day, ticket)
                code = await self.source.create(period)
                ticket = ticket.model_copy(update={"code": code})
                await self.unit_of_work.save_ticket(self.store, period.day, ticket)
            try:
                evidence = await self.source.evidence(period, ticket.code)
            except StorageReportFailedError:
                await self.unit_of_work.save_ticket(
                    self.store, period.day, ticket.model_copy(update={"code": ""})
                )
                raise
            articles = await self.unit_of_work.articles(self.store)
            now = self.clock()
            if moscow_day(now) != period.day or evidence.period != period:
                raise ValueError("Storage acquisition period changed")
            snapshot = normalize(self.store, evidence, articles, now)
            await self.unit_of_work.commit(snapshot)
            return StorageSyncResult(stores=1, products=len(snapshot.products))
        except Exception as error:
            await self.unit_of_work.fail(self.store, period.day, type(error).__name__)
            raise
