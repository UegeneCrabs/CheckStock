from app.core.constants import STOCK_READ, Marketplace, StockScheme
from app.yandex import tokens


def scheme_keys_for(marketplace: str, store_slug: str = "") -> tuple[str, ...]:
    if marketplace == Marketplace.YANDEX_MARKET:
        keys = (
            {campaign["scheme_key"] for campaign in tokens.get_campaigns(store_slug)} if store_slug else set()
        )
        return tuple(sorted(keys, key=lambda key: (key != StockScheme.FBO, key))) or STOCK_READ.yandex_schemes
    if marketplace == Marketplace.OZON:
        return STOCK_READ.ozon_schemes
    return STOCK_READ.wb_schemes


def fbs_schemes_for(marketplace: str, store_slug: str = "") -> tuple[str, ...]:
    return tuple(
        scheme
        for scheme in scheme_keys_for(marketplace, store_slug)
        if scheme == StockScheme.FBS or scheme.startswith(StockScheme.FBS + "_")
    )
