from app.core.constants import Marketplace
from app.dto.google_stock_report import StockTestCatalog, StockTestCatalogProduct
from app.exports.project_scope import stores_for_marketplace
from app.repositories.catalog import get_catalog_items


def order_catalog(marketplace: Marketplace) -> StockTestCatalog:
    return StockTestCatalog(
        products=tuple(
            StockTestCatalogProduct(
                store=store,
                article=article,
                barcode=str(row.get("barcode") or "").strip(),
                name=str(row.get("name") or "").strip(),
            )
            for store in stores_for_marketplace(marketplace.value)
            for row in get_catalog_items(store, marketplace.value)
            if (article := str(row.get("article") or "").strip())
        )
    )
