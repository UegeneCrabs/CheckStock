from app.core.constants import (
    ORDER_NORMALIZATION,
    OrderScheme,
)
from app.dto.orders import OzonOrderPosting, OzonOrderProduct
from app.orders.normalizers.common import compact_json, integer, number, timestamp


def _prepare(posting: dict, posting_index: int) -> OzonOrderPosting:
    key = str(
        posting.get("posting_number")
        or posting.get("order_number")
        or posting.get("order_id")
        or f"{ORDER_NORMALIZATION.legacy_order_key_prefix}{posting_index}"
    )
    status = str(posting.get("status") or "").lower()
    cancellation = posting.get("cancellation") or {}
    ordered = timestamp(
        posting.get("created_at") or posting.get("in_process_at") or posting.get("shipment_date")
    )
    updated = timestamp(posting.get("updated_at") or posting.get("status_updated_at") or ordered)
    analytics = posting.get("analytics_data") or {}
    sold = (
        posting.get("delivering_date")
        or analytics.get("delivery_date_end")
        or posting.get("shipment_date")
        or updated
    )
    financial = posting.get("financial_data") or {}
    finances = {}
    for row in financial.get("products") or posting.get("financial_products") or []:
        product_key = str(row.get("product_id") or row.get("sku") or row.get("offer_id") or "")
        if product_key:
            finances[product_key] = row
    products = []
    for index, product in enumerate(posting.get("products") or posting.get("items") or []):
        product_key = str(product.get("sku") or product.get("product_id") or product.get("offer_id") or index)
        finance = finances.get(product_key) or {}
        products.append(
            OzonOrderProduct(
                key=product_key,
                article=str(product.get("offer_id") or product.get("product_id") or ""),
                barcode=str(product.get("barcode") or ""),
                name=str(product.get("name") or ""),
                quantity=max(
                    integer(product.get("quantity"), ORDER_NORMALIZATION.legacy_default_quantity),
                    ORDER_NORMALIZATION.legacy_default_quantity,
                ),
                unit_price=number(
                    product.get("price"), number(finance.get("price"), number(finance.get("old_price")))
                ),
                currency=str(product.get("currency_code") or ORDER_NORMALIZATION.default_currency),
            )
        )
    return OzonOrderPosting(
        key=key,
        external_order_id=str(posting.get("order_number") or posting.get("order_id") or key),
        status=status,
        substatus=str(posting.get("substatus") or ""),
        cancelled=ORDER_NORMALIZATION.cancellation_status_marker in status
        or bool(cancellation.get("cancel_reason_id")),
        delivered=status in ORDER_NORMALIZATION.delivered_statuses,
        ordered_at=ordered,
        updated_at=updated,
        sold_at=timestamp(sold) if status in ORDER_NORMALIZATION.delivered_statuses else updated,
        products=tuple(products),
        raw_json=compact_json(posting),
    )


def normalize(store_slug: str, postings: list[dict], scheme: str) -> list[dict]:
    from app.orders.normalizers.ozon import normalize_postings

    try:
        order_scheme = OrderScheme(scheme)
    except ValueError:
        raise ValueError("Unknown Ozon order scheme") from None
    return [
        line.model_dump(mode="json", exclude={"returned_at", "return_quantity", "return_amount"})
        for line in normalize_postings(
            store_slug, tuple(_prepare(p, i) for i, p in enumerate(postings)), order_scheme
        )
    ]
