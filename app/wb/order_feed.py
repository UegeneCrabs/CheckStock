from datetime import datetime

from pydantic import AwareDatetime, ConfigDict, Field, PositiveInt

from app.core.constants import WB_ORDER_FEED
from app.dto.common import DtoModel
from app.wb.api import ANALYTICS_BASE, WBApiError, request


class FeedOrder(DtoModel):
    model_config = ConfigDict(extra="ignore")

    article: PositiveInt = Field(alias="nmId")
    srid: str = Field(min_length=1)
    created_at: AwareDatetime = Field(alias="createdAt")
    updated_at: AwareDatetime = Field(alias="updatedAt")
    status: str


class FeedPage(DtoModel):
    model_config = ConfigDict(extra="ignore")

    snapshot_time: str = Field(default="", alias="snapshotTime")
    orders: tuple[FeedOrder, ...]


def get_orders(token: str, start: datetime, end: datetime, max_pages: int) -> tuple[FeedOrder, ...]:
    snapshot = ""
    orders: dict[str, FeedOrder] = {}
    for _ in range(max_pages):
        pagination: dict[str, int | str] = {"offset": len(orders), "limit": WB_ORDER_FEED.page_size}
        if snapshot:
            pagination["snapshotTime"] = snapshot
        result = request(
            "POST",
            ANALYTICS_BASE + WB_ORDER_FEED.path,
            token,
            json_body={
                "selectedPeriod": {"start": start.isoformat(), "end": end.isoformat()},
                "pagination": pagination,
            },
        )
        if not isinstance(result, dict):
            raise WBApiError(None, detail="WB не вернул ленту заказов")
        page = FeedPage.model_validate(result.get("data"))
        if snapshot and page.snapshot_time and snapshot != page.snapshot_time:
            raise WBApiError(None, detail="WB изменил снимок ленты заказов между страницами")
        for order in page.orders:
            if order.srid in orders or order.status not in WB_ORDER_FEED.statuses:
                raise WBApiError(None, detail="WB повторил заказ или вернул неизвестный статус")
            orders[order.srid] = order
        if len(page.orders) < WB_ORDER_FEED.page_size:
            return tuple(orders.values())
        snapshot = snapshot or page.snapshot_time
        if not snapshot:
            raise WBApiError(None, detail="WB не вернул идентификатор снимка ленты заказов")
    raise WBApiError(None, detail="Превышен предел страниц ленты заказов WB")
