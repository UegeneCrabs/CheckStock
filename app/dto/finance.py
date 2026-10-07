"""Explicit boundaries for financial imports and read models."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

Source = Literal["realization", "services", "orders", "payments"]


class FinanceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1, max_length=300)
    day: date
    kind: Literal["sale", "return", "expense", "income", "order", "payment", "unknown"]
    campaign_id: int | None = None
    order_id: str = ""
    article: str = ""
    line_id: str = ""
    quantity: int = Field(default=0, ge=0)
    seller: Decimal | None = None
    buyer: Decimal | None = None
    amount: Decimal | None = None
    cost: Decimal | None = None
    cost_origin: str = ""
    category: str = ""
    source: Source
    source_sheet: str = ""
    original_day: date | None = None
    issues: tuple[str, ...] = ()
    currency: Literal["RUB"] = "RUB"

    @field_validator("seller", "buyer", "amount", "cost")
    @classmethod
    def finite(cls, value):
        if value is not None and not value.is_finite():
            raise ValueError("Сумма должна быть конечным числом")
        return value

    @model_validator(mode="after")
    def item_identity(self):
        if self.kind in {"sale", "return"} and (
            not self.article or not self.order_id or self.quantity < 1 or not self.campaign_id
        ):
            raise ValueError("Реализация требует кампанию, заказ, артикул и количество")
        return self


class SourceBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Source
    start: date
    end: date
    events: tuple[FinanceEvent, ...] = ()
    unallocated: tuple[FinanceEvent, ...] = ()
    income_issues: tuple[str, ...] = ()
    raw: dict = Field(default_factory=dict)
    report_ids: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()

    @model_validator(mode="after")
    def bounds(self):
        if (
            self.start > self.end
            or self.start.day != 1
            or self.start.strftime("%Y-%m") != self.end.strftime("%Y-%m")
        ):
            raise ValueError("Снимок должен описывать один календарный месяц с первого числа")
        for group in (self.events, self.unallocated):
            keys = [event.key for event in group]
            if len(keys) != len(set(keys)):
                raise ValueError("Повтор ключа финансовой операции")
        if any(
            event.source != self.source or not self.start <= event.day <= self.end
            for event in (*self.events, *self.unallocated)
        ):
            raise ValueError("Операция вне области снимка")
        if any(event.campaign_id is not None for event in self.unallocated):
            raise ValueError("Нераспределённая операция не должна содержать кампанию")
        return self


class ConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store_slug: str = Field(min_length=1, max_length=100)
    business_id: int = Field(gt=0)
    campaign_ids: list[int] = Field(min_length=1, max_length=50)
    api_key: SecretStr = SecretStr("")
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def valid_scope(self):
        if any(x <= 0 for x in self.campaign_ids) or len(set(self.campaign_ids)) != len(self.campaign_ids):
            raise ValueError("Укажите уникальные положительные Campaign ID")
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("Некорректный интервал подключения")
        if len(self.api_key.get_secret_value()) > 16384:
            raise ValueError("Ключ слишком длинный")
        return self


class FinanceRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store_slug: str
    date_from: date
    date_to: date
    mode: Literal["import", "recalculate", "retry"] = "import"

    @model_validator(mode="after")
    def period(self):
        if self.date_from > self.date_to or (self.date_to - self.date_from).days >= 366:
            raise ValueError("Период должен составлять от 1 до 366 дней")
        return self


class CostUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store_slug: str
    article: str = Field(min_length=1, max_length=250)
    effective_from: date
    effective_to: date | None = None
    price: Decimal = Field(ge=0, max_digits=18, decimal_places=4, allow_inf_nan=False)
    reason: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def period(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("Некорректный период цены")
        return self
