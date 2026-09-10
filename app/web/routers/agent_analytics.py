"""Small read-only API for ChatGPT Actions, reusing the website's report and ACLs."""

import logging
import math
import os
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.access_control import accessible_stores
from app.agent_access import resolve_credential
from app.config import settings
from app.domain import MOSCOW_TIMEZONE
from app.dto.identity import SectionName, User, UserId
from app.section_access import has_access
from app.stores import STORES
from app.web.routers.unit_economics import _unit_economics_1c_unit_profit_report_data

PREFIX = "/api/agent/v1"
router = APIRouter(prefix=PREFIX, tags=["Employee analytics"])
bearer = HTTPBearer(auto_error=False, scheme_name="EmployeeApiKey")
logger = logging.getLogger(__name__)


async def employee(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    record = (
        await run_in_threadpool(resolve_credential, credentials.credentials, settings.agent_tokens_path)
        if credentials is not None
        else None
    )
    user = (
        await run_in_threadpool(request.app.state.container.identity.get_user, UserId(record.user_id))
        if record is not None
        else None
    )
    if user is None or not user.is_active:
        raise HTTPException(
            401, "Invalid or expired employee API key", headers={"WWW-Authenticate": "Bearer"}
        )
    if not any(has_access(user, section) for section in SectionName):
        raise HTTPException(403, "No access to analytics sections")
    request.state.user = user
    logger.info("agent_access user_id=%s key_id=%s path=%s", user.id, record.key_id, request.url.path)
    return user


Employee = Annotated[User, Depends(employee)]


class StoreInfo(BaseModel):
    slug: str
    name: str
    marketplace: str = "WB"
    marketplaces: list[str] = Field(default_factory=list)


class LossQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: date
    date_to: date
    store: str = Field(min_length=1, max_length=100, description="Store slug from listAnalyticsStores")
    limit: int = Field(default=20, ge=1, le=100)
    article: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("store")
    @classmethod
    def normalize_store(cls, value: str) -> str:
        return value.strip().lower()


class LossRow(BaseModel):
    store: str
    article: str
    name: str
    estimated_profit_rub: float
    orders_count: int | None
    orders_amount_rub: float | None
    advertising_spend_rub: float | None
    roi_percent: float | None
    orders_updated_at: str | None
    buyouts_updated_at: str | None


class LossReport(BaseModel):
    marketplace: Literal["WB"] = "WB"
    currency: Literal["RUB"] = "RUB"
    metric: Literal["estimated_period_profit"] = "estimated_period_profit"
    date_from: date
    date_to: date
    generated_at: datetime
    total_products_checked: int
    excluded_incomplete_products: int
    total_loss_products: int
    rows: list[LossRow]
    warnings: list[str]


@router.get("/stores", operation_id="listAnalyticsStores", response_model=list[StoreInfo])
async def stores(user: Employee):
    """List accessible stores and marketplaces. Permissions do not imply data is loaded."""
    from app.access_control import accessible_marketplaces

    return [StoreInfo(slug=slug, name=STORES[slug]["name"],
                      marketplace=next(iter(accessible_marketplaces(user, slug)), "WB"),
                      marketplaces=list(accessible_marketplaces(user, slug)))
            for slug in accessible_stores(user)]


def finite_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def summarize_losses(report: dict, query: LossQuery) -> LossReport:
    rows = []
    excluded = 0
    for source in report["rows"]:
        profit = finite_number(source.get("margin"))
        if source.get("margin_complete") is not True or profit is None:
            excluded += 1
            continue
        if profit >= 0:
            continue
        orders = finite_number(source.get("orders_count"))
        rows.append(
            LossRow(
                store=source["store_slug"],
                article=source["article"],
                name=str(source["name"])[:200],
                estimated_profit_rub=profit,
                orders_count=int(orders) if orders is not None else None,
                orders_amount_rub=finite_number(source.get("orders_amount")),
                advertising_spend_rub=finite_number(source.get("advertising_spend")),
                roi_percent=finite_number(source.get("roi")),
                orders_updated_at=source.get("funnel_updated_at"),
                buyouts_updated_at=source.get("buyout_updated_at"),
            )
        )
    rows.sort(key=lambda row: (row.estimated_profit_rub, row.store, row.article))
    warnings = [
        "Это расчётная прибыль за период по методике отчёта «Юниточная прибыль», "
        "с ожидаемыми выкупами, а не подтверждённый финансовый результат.",
        "margin_complete в исходном отчёте характеризует покрытие расчёта маржи, "
        "но не гарантирует полноту всех внешних источников. Отсутствие строк не доказывает отсутствие убытков.",
        "generated_at — время ответа API. Даты обновления заказов и выкупов указаны отдельно; "
        "свежесть остальных источников этим API не подтверждается.",
    ]
    if excluded:
        warnings.append(f"Исключено товаров с неполным расчётом или неизвестной прибылью: {excluded}.")
    return LossReport(
        date_from=query.date_from,
        date_to=query.date_to,
        generated_at=datetime.now(UTC),
        total_products_checked=len(report["rows"]),
        excluded_incomplete_products=excluded,
        total_loss_products=len(rows),
        rows=rows[: query.limit],
        warnings=warnings,
    )


@router.get("/loss-products", operation_id="getLossMakingProducts", response_model=LossReport)
async def losses(request: Request, user: Employee, query: Annotated[LossQuery, Query()]):
    """Rank WB products by lowest estimated total period profit in RUB.

    Requires store and dates. Excludes incomplete margin calculations. Optional article filter.
    Disclose warnings: this is estimated profit, not confirmed financial loss.
    """
    from app.web.routers.agent_full import guard

    guard(user, SectionName.UNIT_ECONOMICS_1C,
          SimpleNamespace(store=query.store, marketplace="WB"))
    if query.date_from > query.date_to or (query.date_to - query.date_from).days >= 90:
        raise HTTPException(422, "Choose an ordered period of 1 to 90 days")
    if query.date_to > datetime.now(MOSCOW_TIMEZONE).date():
        raise HTTPException(422, "Future dates are not supported")
    if query.store not in accessible_stores(user, "WB"):
        raise HTTPException(403, "No access to this WB store")
    params = {
        "date_from": query.date_from.isoformat(),
        "date_to": query.date_to.isoformat(),
        "store": query.store,
    }
    if query.article:
        params["article"] = query.article
    scoped_request = Request({**request.scope, "query_string": urlencode(params).encode()})
    report = await _unit_economics_1c_unit_profit_report_data(scoped_request)
    if isinstance(report, JSONResponse):
        return report
    return summarize_losses(report, query)


@router.get("/openapi.json", include_in_schema=False)
async def action_schema():
    from app.web.routers.agent_full import PERIOD_REPORTS, SPECS, allowed_fields

    schema = get_openapi(title="CheckStock employee analytics", version="2.0.0", routes=router.routes)
    for path, item in schema["paths"].items():
        name = path.rsplit("/", 1)[-1]
        fields = allowed_fields(name) if name in SPECS else {"store", "marketplace"} if name == "data-status" else None
        if fields is not None:
            item["get"]["parameters"] = [p for p in item["get"].get("parameters", []) if p["name"] in fields]
        for parameter in item["get"].get("parameters", []):
            # URL query parameters are omitted when unset, never JSON null.
            query_schema = parameter.get("schema", {})
            variants = query_schema.get("anyOf", [])
            non_null = [variant for variant in variants if variant.get("type") != "null"]
            if len(non_null) == 1 and len(variants) == 2:
                parameter["schema"] = {
                    **{key: value for key, value in query_schema.items() if key != "anyOf"},
                    **non_null[0],
                }
                if parameter["schema"].get("default") is None:
                    parameter["schema"].pop("default", None)
            if name in PERIOD_REPORTS and parameter["name"] in {"date_from", "date_to"}:
                parameter["required"] = True
                parameter["description"] = "Required inclusive date in YYYY-MM-DD format. Supply both dates; at most 90 days."
            if name == "rnp" and parameter["name"] == "month":
                parameter["required"] = True
            if name == "rnp" and parameter["name"] == "limit":
                parameter["schema"]["maximum"] = 20
                parameter["description"] = "RNP page size, from 1 to 20. Use next_offset for further pages."
            if name == "product-details" and parameter["name"] == "article":
                parameter["required"] = True
            if parameter["name"] == "store" and (name in SPECS or name == "data-status"):
                parameter["required"] = name == "data-status"
                parameter["schema"] = {"type": "string", "minLength": 1, "maxLength": 100}
                parameter["description"] = "Optional with exact article: server resolves store automatically. Omit unknown store; never guess." if name in SPECS else "Store slug"
        if name in SPECS:
            item["get"]["description"] = item["get"].get("description", "") + " With article, omit unknown store for automatic resolution; see context.store_resolution if ambiguous."
        if name == "profit-calculator":
            for parameter in item["get"]["parameters"]:
                if parameter["name"] == "article":
                    parameter["required"] = True
                    parameter["schema"] = {"type": "string", "minLength": 1, "maxLength": 100}
                    parameter["description"] = "Exact product article, e.g. 856546716. Ask the user if missing."
    public_url = os.getenv("CHECKSTOCK_AGENT_PUBLIC_URL", "").strip().rstrip("/")
    schema["servers"] = [{"url": public_url}] if public_url else []
    return schema
