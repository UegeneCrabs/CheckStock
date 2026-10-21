"""Adapt the legacy calculator catalogue's JSON contract to report input types."""

from app.dto.wb_standard_costs import (
    CalculatorProduct,
    Coverage,
    ProductAdvertising,
    ProductDetails,
    ProductEconomics,
    ProductPrice,
    ProductSettings,
)
from app.economics.wb.standard_costs import number


def calculator_product(source: dict) -> CalculatorProduct:
    details = source.get("details") or {}
    price = source.get("price") or {}
    ads = source.get("advertising") or {}
    settings = source.get("product_settings") or {}
    economics = source.get("economics_7d") or {}
    # Select only this report's fields. Unknown catalogue fields are unrelated to its contract.
    detail_values = {
        key: number(details[key])
        for key in ProductDetails.model_fields
        if key in details and key != "tax_system"
    }
    if "tax_system" in details:
        detail_values["tax_system"] = details["tax_system"]
    return CalculatorProduct(
        article=str(source.get("article") or ""),
        manager=source.get("manager"),
        image_url=source.get("image_url"),
        details=ProductDetails.model_validate(detail_values),
        price=ProductPrice(
            current=number(price.get("current")),
            with_spp=number(price.get("with_spp")),
            snapshot_date=price.get("snapshot_date"),
        ),
        advertising=ProductAdvertising(
            spend=number(ads.get("spend")),
            orders=number(ads.get("orders")),
            buyout_percent=number(ads.get("buyout_percent")),
            spend_per_order=number(ads.get("spend_per_order")),
            coverage=Coverage.model_validate(ads["coverage"]) if ads.get("coverage") else None,
        ),
        product_settings=ProductSettings(storage_wb_rub=number(settings.get("storage_wb_rub"))),
        economics_7d=ProductEconomics(
            net_orders_count=number(ads.get("net_orders_count")),
            turnover=number(economics.get("turnover")),
            turnover_coverage=Coverage.model_validate(economics["turnover_coverage"])
            if economics.get("turnover_coverage")
            else None,
        ),
        data_errors=source.get("data_errors") or [],
    )
