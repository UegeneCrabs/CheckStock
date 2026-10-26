from datetime import date, timedelta

from app.application.ports import OzonHistoryReader
from app.core.constants import OZON_HISTORY
from app.dto.ozon_advertising import AdvertisingPeriod
from app.dto.ozon_history import OzonHistory, OzonHistoryQuery
from app.ozon.history_normalizer import history_days


class OzonHistoryService:
    def __init__(self, reader: OzonHistoryReader):
        self.reader = reader

    async def history(self, query: OzonHistoryQuery, today: date) -> OzonHistory:
        start = today - timedelta(days=OZON_HISTORY.visible_days + OZON_HISTORY.comparison_days - 1)
        evidence = await self.reader.load(query, start, today)
        return OzonHistory(
            today=today,
            period_from=today - timedelta(days=OZON_HISTORY.visible_days - 1),
            period_to=today,
            days=history_days(query, AdvertisingPeriod(start=start, end=today), evidence),
        )
