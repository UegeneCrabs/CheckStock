from datetime import date, datetime

from app.application.ports import YandexDailyPriceCloser
from app.core.domain import MOSCOW_TIMEZONE
from app.core.logging_config import tagged_logger

logger = tagged_logger(__name__, "yandex", "price_history")


class YandexPriceHistoryService:
    def __init__(self, repository: YandexDailyPriceCloser) -> None:
        self.repository = repository

    def close(self, today: date | None = None) -> int:
        count = self.repository.close(today or datetime.now(MOSCOW_TIMEZONE).date())
        logger.info("yandex_daily_prices_closed rows=%s", count)
        return count
