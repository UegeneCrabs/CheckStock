import unicodedata
from decimal import Decimal

from app.core.constants import OZON_TARIFFS
from app.dto.ozon_tariffs import OzonCategoryIdentity, OzonCategoryNode, OzonTariff, TariffId


def normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def match_tariff(type_name: str, items: tuple[OzonTariff, ...]) -> TariffId | None:
    matches = [item.id for item in items if normalized_name(item.type_name) == normalized_name(type_name)]
    return matches[0] if len(matches) == 1 else None


def category_identities(
    nodes: tuple[OzonCategoryNode, ...], category_id: int = 0, category_name: str = ""
) -> tuple[OzonCategoryIdentity, ...]:
    items: list[OzonCategoryIdentity] = []
    for node in nodes:
        current_id = node.description_category_id or category_id
        current_name = node.category_name or category_name
        if node.type_id:
            items.append(
                OzonCategoryIdentity(
                    category_id=current_id,
                    category_name=current_name,
                    type_id=node.type_id,
                    type_name=node.type_name,
                )
            )
        items.extend(category_identities(node.children, current_id, current_name))
    return tuple(items)


def commission_percent(item: OzonTariff | None, price: Decimal | None) -> Decimal | None:
    if price is None or price <= 0:
        return None
    if price <= OZON_TARIFFS.first_price_limit:
        return item.fbo_up_to_100 if item is not None else Decimal(OZON_TARIFFS.first_price_percent)
    if price <= OZON_TARIFFS.second_price_limit:
        return item.fbo_up_to_300 if item is not None else Decimal(OZON_TARIFFS.second_price_percent)
    return item.fbo_over_300 if item is not None else None
