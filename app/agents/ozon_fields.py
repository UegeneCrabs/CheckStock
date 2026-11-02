from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, JsonValue

from app.dto.ozon_economics import OzonProduct
from app.economics.ozon_calculator import recalculate
from app.economics.ozon_target_prices import calculator_values
from app.ozon.analytics_normalizer import price_amount, ratio


def json_value(value: object) -> JsonValue:
    if isinstance(value, BaseModel):
        return json_value(value.model_dump(mode="python"))
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(type(value).__name__)


def row(value: BaseModel) -> dict[str, JsonValue]:
    result = json_value(value)
    if not isinstance(result, dict):
        raise TypeError(type(result).__name__)
    return result


def product_row(name: str, product: OzonProduct, *, details: bool = False) -> dict[str, JsonValue]:
    identity: dict[str, JsonValue] = {
        "article": product.article,
        "name": product.name,
        "store_slug": product.store_slug,
        "manager": product.manager,
    }
    fields: dict[str, JsonValue]
    if name == "product-newness":
        fields = {
            "is_new": product.is_new,
            "sales_days": product.sales_days,
            "age_known": product.is_new is not None,
            **row(product.newness),
        }
    elif name == "product-reputation":
        fields = {"rating": product.rating, "reviews_count": product.reviews_count}
    elif name == "product-tags":
        fields = row(product.tag_data)
    elif name == "current-economics":
        current = product.current_economics
        seller = price_amount(product.price.seller_price)
        buyer = price_amount(product.price.black_price)
        fields = {
            "margin_per_unit_rub": current.margin,
            "roi_percent": current.roi,
            "calculation_available": current.calculation is not None,
            "calculation_complete": current.complete,
            "as_of_date": current.period_to,
            "has_source_errors": bool(product.data_issues),
            "seller_price": seller,
            "buyer_price": buyer,
            "wallet_price": price_amount(product.price.green_price),
            "spp_percent": ratio(seller - buyer, seller)
            if seller is not None and buyer is not None
            else None,
        }
    elif name == "profit-calculator":
        fields = row(recalculate(calculator_values(product)))
    elif name == "prices":
        fields = {
            key: json_value(getattr(product.price, key))
            for key in ("seller_price", "black_price", "green_price", "base_price")
        }
    elif name == "costs":
        fields = {
            "purchase_price": product.details.purchase_cost,
            "fulfillment_cost": product.details.fulfillment_cost,
            "team_commission_percent": product.details.team_commission_percent,
            "synced_at": product.details.source_synced_at,
            "abc_code": product.tag_data.code,
            "goal_week": product.tag_data.goal_week,
            "goal_day": product.tag_data.goal_day,
            "fact_sales": product.tag_data.fact,
            "plan_sales": product.tag_data.plan,
        }
    elif name == "stock-value":
        quantity, purchase = product.stock.total, product.details.purchase_cost
        fields = {
            "quantity": quantity,
            "purchase_price": purchase,
            "value_rub": quantity * purchase if quantity is not None and purchase is not None else None,
            "cost_missing": purchase is None,
        }
    elif name == "ozon-buyout":
        fields = row(product.cabinet.buyout) if product.cabinet else {"percent": None}
    elif name == "ozon-logistics":
        fields = row(product.logistics)
        snapshot = fields.get("snapshot")
        if not details and isinstance(snapshot, dict):
            snapshot.pop("charges", None)
    elif name == "ozon-storage":
        fields = row(product.storage)
        snapshot = fields.get("snapshot")
        if not details and isinstance(snapshot, dict):
            snapshot.pop("days", None)
    elif name == "ozon-commissions":
        fields = row(product.commission)
    elif name == "ozon-advertising-breakdown":
        fields = {
            "breakdown": json_value(product.advertising.breakdown),
            "spend_summary": json_value(product.advertising.spend_summary),
        }
    elif name == "advertising":
        ad = product.advertising
        fields = {
            "spend": ad.spend,
            "impressions": ad.impressions,
            "clicks": ad.clicks,
            "ctr_percent": ad.ctr,
            "cpc": ad.cpc,
            "drr_percent": ad.drr,
            "coverage": json_value(ad.coverage),
            "traffic_coverage": json_value(ad.traffic_coverage),
            "drr_coverage": json_value(ad.drr_coverage),
        }
    else:
        raise ValueError(name)
    return {**identity, **fields, "data_issues": json_value(product.data_issues)}
