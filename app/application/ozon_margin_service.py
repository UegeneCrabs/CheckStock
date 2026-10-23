from datetime import UTC, datetime

from app.application.ports import OzonMarginStore
from app.dto.ozon_margin import OzonMarginSyncResult


class OzonMarginService:
    def __init__(self, repository: OzonMarginStore):
        self.repository = repository

    async def capture(self, store: str) -> OzonMarginSyncResult:
        values = await self.repository.current((store,), datetime.now(UTC))
        await self.repository.save(values, datetime.now(UTC))
        return OzonMarginSyncResult(products=len(values))
