from app.core.constants import ORDER_NORMALIZATION, OrderScheme
from app.dto.orders import OzonOrderPosting, SalesOrderLine
from app.orders.normalizers.ozon_legacy import normalize as normalize


def normalize_postings(
    store_slug: str, postings: tuple[OzonOrderPosting, ...], scheme: OrderScheme
) -> tuple[SalesOrderLine, ...]:
    lines = []
    for posting in postings:
        for product in posting.products:
            amount = round(product.unit_price * product.quantity, ORDER_NORMALIZATION.amount_decimal_places)
            sold = posting.delivered and not posting.cancelled
            lines.append(
                SalesOrderLine(
                    store_slug=store_slug,
                    order_key=posting.key,
                    line_key=product.key,
                    external_order_id=posting.external_order_id,
                    scheme=scheme,
                    status=posting.status,
                    substatus=posting.substatus,
                    article=product.article,
                    barcode=product.barcode,
                    name=product.name,
                    ordered_at=posting.ordered_at,
                    source_updated_at=posting.updated_at,
                    cancelled_at=posting.updated_at if posting.cancelled else None,
                    sold_at=posting.sold_at if posting.delivered else None,
                    quantity=product.quantity,
                    cancelled_quantity=product.quantity if posting.cancelled else 0,
                    sold_quantity=product.quantity if sold else 0,
                    order_amount=amount,
                    cancelled_amount=amount if posting.cancelled else 0.0,
                    sale_amount=amount if sold else 0.0,
                    currency=product.currency,
                    raw_json=posting.raw_json,
                )
            )
    return tuple(lines)
