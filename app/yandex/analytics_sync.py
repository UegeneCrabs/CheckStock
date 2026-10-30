import re
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr

from app.core.constants import YANDEX_ANALYTICS
from app.dto.yandex_analytics import (
    AnalyticsModel,
    AnalyticsSourceError,
    DailyMetric,
    FunnelDateComponents,
    FunnelDateMode,
    FunnelDay,
    FunnelRequest,
    ParsedFunnelDate,
    TransitSnapshot,
)
from app.yandex import api, tokens
from app.yandex.accounts import resolve_business_id
from app.yandex.unit_economics_sync import load_report


class FunnelItem(AnalyticsModel):
    offerId: StrictStr = Field(min_length=1)
    toCart: StrictInt | None
    day: StrictStr | None = None
    month: StrictStr | None = None
    year: StrictInt | None = None


class ReturnInstance(AnalyticsModel):
    status: StrictStr


class ReturnItem(AnalyticsModel):
    shopSku: StrictStr = Field(min_length=1)
    count: StrictInt = Field(ge=0)
    instances: tuple[ReturnInstance, ...] = ()


class ReturnShipment(AnalyticsModel):
    id: StrictInt = Field(gt=0)
    orderId: StrictInt = Field(gt=0)
    shipmentStatus: StrictStr
    fastReturn: StrictBool = False
    items: tuple[ReturnItem, ...]


def parse_funnel_date(item: FunnelItem, expected: date) -> ParsedFunnelDate:
    mode: FunnelDateMode = "request_only"
    parsed = expected
    if "day" in item.model_fields_set:
        value = item.day
        if value is not None and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            parsed = date.fromisoformat(value)
            mode = "iso_date"
        elif value is not None and re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}", value):
            parsed = datetime.strptime(value, "%d.%m.%Y").date()
            mode = "local_date"
        elif value is not None and re.fullmatch(r"[0-9]{2}-[0-9]{2}-[0-9]{4}", value):
            parsed = datetime.strptime(value, "%d-%m-%Y").date()
            mode = "local_dash_date"
        elif value is not None and re.fullmatch(r"[0-9]{1,2}", value):
            parsed = date(expected.year, expected.month, int(value))
            mode = "day_of_month"
        else:
            raise AnalyticsSourceError("funnel_day_format_unknown", field="day", value=value)
    if "month" in item.model_fields_set:
        match = re.fullmatch(r"([0-9]{1,2})(?:-([0-9]{4}))?", item.month) if item.month else None
        if match is None:
            raise AnalyticsSourceError("funnel_month_format_unknown", field="month", value=item.month)
        month = int(match[1])
        if match[2] is not None and (int(match[2]) != expected.year or int(match[2]) != parsed.year):
            raise AnalyticsSourceError("funnel_source_year_mismatch", field="month", value=item.month)
        if month != expected.month or month != parsed.month:
            raise ValueError("funnel_source_month_mismatch")
    if "year" in item.model_fields_set:
        if item.year is None or item.year != expected.year or item.year != parsed.year:
            raise ValueError("funnel_source_year_mismatch")
    if parsed != expected:
        raise ValueError("funnel_source_date_mismatch")
    return ParsedFunnelDate(source_date=parsed, mode=mode)


def normalize_funnel(
    store: str,
    source_date: date,
    rows: object,
    received_at: datetime,
    *,
    request: FunnelRequest | None = None,
) -> FunnelDay:
    if request is None or request.date_from != source_date or request.date_to != source_date:
        raise ValueError("funnel_single_day_unverified")
    if not isinstance(rows, list):
        raise ValueError("invalid_funnel_rows")
    products: list[DailyMetric] = []
    seen: set[str] = set()
    for raw in rows:
        item = FunnelItem.model_validate(raw)
        parsed = parse_funnel_date(item, source_date)
        origin: Literal["explicit_day", "single_day_request"] = (
            "explicit_day" if "day" in item.model_fields_set else "single_day_request"
        )
        if item.offerId in seen or item.toCart is not None and item.toCart < 0:
            raise ValueError("invalid_funnel_item")
        seen.add(item.offerId)
        products.append(
            DailyMetric(
                article=item.offerId,
                day=source_date.isoformat(),
                cart_count=item.toCart,
                timezone="unknown",
                received_at=received_at,
                provisional=True,
                source_scope="report_date",
                source_date_origin=origin,
                source_date_parse_mode=parsed.mode,
                source_date_components=FunnelDateComponents(
                    day_present="day" in item.model_fields_set,
                    day=item.day,
                    month_present="month" in item.model_fields_set,
                    month=item.month,
                    year_present="year" in item.model_fields_set,
                    year=item.year,
                ),
            )
        )
    return FunnelDay(
        store=store,
        source_date=source_date,
        source_date_origin="single_day_request",
        request=request,
        received_at=received_at,
        products=tuple(products),
    )


def load_funnel(store: str, source_date: date) -> FunnelDay:
    api_key = tokens.get_api_key(store)
    business_id = resolve_business_id(store, api_key)
    query = FunnelRequest(date_from=source_date, date_to=source_date, grouping="OFFERS")
    rows = load_report(
        api_key,
        "shows-sales",
        {
            "businessId": business_id,
            "dateFrom": query.date_from.isoformat(),
            "dateTo": query.date_to.isoformat(),
            "grouping": query.grouping,
        },
        "sales_funnel_report",
        "offerId",
        strict_rows=True,
    )
    return normalize_funnel(store, source_date, rows, datetime.now(UTC), request=query)


def normalize_returns(rows: object) -> dict[str, int]:
    if not isinstance(rows, list):
        raise ValueError("invalid_returns_rows")
    result: dict[str, int] = defaultdict(int)
    seen: set[tuple[int, int]] = set()
    for raw in rows:
        shipment = ReturnShipment.model_validate(raw)
        identity = (shipment.orderId, shipment.id)
        if identity in seen:
            raise ValueError("duplicate_return")
        seen.add(identity)
        if shipment.shipmentStatus not in YANDEX_ANALYTICS.shipment_statuses:
            raise AnalyticsSourceError(
                "unknown_return_shipment_status", field="shipmentStatus", value=shipment.shipmentStatus
            )
        for item in shipment.items:
            for unit in item.instances:
                if unit.status not in YANDEX_ANALYTICS.return_instance_statuses:
                    raise AnalyticsSourceError(
                        "unknown_return_instance_status", field="instances.status", value=unit.status
                    )
            if item.instances and len(item.instances) != item.count:
                raise ValueError("incomplete_return_instances")
        if shipment.fastReturn or shipment.shipmentStatus not in YANDEX_ANALYTICS.moving_statuses:
            continue
        for item in shipment.items:
            count = (
                sum(unit.status in YANDEX_ANALYTICS.moving_statuses for unit in item.instances)
                if item.instances
                else item.count
            )
            result[item.shopSku] += count
    return dict(result)


def load_transit(store: str) -> TransitSnapshot:
    api_key = tokens.get_api_key(store)
    campaigns = api.get_campaigns(api_key)
    business_id = resolve_business_id(store, api_key, campaigns=campaigns)
    own = [api.normalize_campaign(row) for row in campaigns]
    own = [row for row in own if row["business_id"] == business_id]
    if not own:
        raise ValueError("own_campaigns_unconfirmed")
    campaign_ids = sorted({int(row["campaign_id"]) for row in own if row["scheme"] == "fby"})
    returns: list[object] = []
    for campaign_id in campaign_ids:
        returns.extend(api.get_fby_customer_returns(api_key, campaign_id))
    return TransitSnapshot(store=store, quantities=normalize_returns(returns), received_at=datetime.now(UTC))
