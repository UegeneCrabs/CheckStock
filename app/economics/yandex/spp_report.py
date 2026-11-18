from datetime import date

from app.dto.identity import User
from app.dto.spp_report import SppCatalogItem
from app.economics import spp_report as shared
from app.economics.yandex import reports
from app.infrastructure.spp_report_repository import SppHistoryReader
from app.infrastructure.yandex_spp_turnover_repository import load_turnover


def _turnover_key(article: str) -> str:
    return article


class YandexSppSource(SppHistoryReader):
    def __init__(self, user: User) -> None:
        self.user = user
        super().__init__(yandex=True, turnover=load_turnover)

    def _catalog(self, stores: tuple[str, ...]) -> tuple[SppCatalogItem, ...]:
        return tuple(
            SppCatalogItem(
                store_slug=row["store_slug"],
                article=str(row["article"]),
                name=str(row.get("name") or ""),
                subject=row.get("subject"),
                manager=row.get("manager"),
            )
            for row in reports.catalog(stores, self.user)
        )

    _turnover = staticmethod(load_turnover)
    _turnover_key = staticmethod(_turnover_key)

    def load(
        self,
        stores: tuple[str, ...],
        date_from: date,
        date_to: date,
        filters: shared.SppFilters,
        *,
        manager_scope: str | None = None,
        tolerance: float = 0,
        page: int = 1,
        page_size: int = 50,
        sort_by: shared.SppSortField = shared.SppSortField.TURNOVER,
        sort_order: shared.SppSortOrder = shared.SppSortOrder.DESC,
    ) -> shared.SppReportResult:
        return shared.load(
            stores,
            date_from,
            date_to,
            filters,
            source=self,
            manager_scope=manager_scope,
            tolerance=tolerance,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    def hourly_history(self, store: str, article: str, day: date) -> shared.SppHourlyHistory:
        return shared.hourly_history(store, article, day, source=self)
