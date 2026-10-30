"""Read saved history through an explicitly supplied repository."""

from app.application.ports import SppHistoryRepository
from app.dto.spp_history import DailySppRow, HourlySppRow, SppHistoryQuery


class SppHistoryService:
    def __init__(self, repository: SppHistoryRepository) -> None:
        self.repository = repository

    def read(self, query: SppHistoryQuery) -> list[HourlySppRow] | list[DailySppRow]:
        if query.date_from > query.date_to:
            raise ValueError("Начало периода позже конца")
        return self.repository.read(query)
