"""Persist actual hourly WB storefront price observations."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from time import monotonic

from app.application.ports import HourlyPriceWriter
from app.dto.wb_hourly_prices import (
    WbCatalogItem,
    WbHourlyCollection,
    WbHourlyPriceObservation,
    WbHourlyStoreReport,
    WbSellerPrice,
    WbStorefrontResult,
    WbStorefrontSize,
)

logger = logging.getLogger(__name__)


def storefront_result_from_payload(response: dict) -> WbStorefrontResult:
    """Parse the public WB response before it enters the hourly service."""
    sizes: list[WbStorefrontSize] = []
    for product in response.get("products") or []:
        if not isinstance(product, dict):
            continue
        nm_id = str(product.get("id") or product.get("nmID") or product.get("nmId") or "")
        if not nm_id.isdigit() or not isinstance(product.get("sizes"), list):
            continue
        for size in product["sizes"]:
            if not isinstance(size, dict) or not isinstance(size.get("price"), dict):
                continue
            price = size["price"]
            try:
                product_kopecks = int(price["product"])
                logistics_kopecks = int(price.get("logistics") or 0)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if product_kopecks <= 0 or logistics_kopecks < 0:
                continue
            try:
                sale_conditions = int(size.get("saleConditions") or 0)
            except (TypeError, ValueError, OverflowError):
                sale_conditions = 0
            try:
                size_id = int(size["optionId"]) if size.get("optionId") is not None else None
            except (TypeError, ValueError, OverflowError):
                size_id = None
            sizes.append(
                WbStorefrontSize(
                    nm_id=nm_id,
                    size_id=size_id,
                    tech_size_name=str(size.get("origName") or size.get("name") or "").strip(),
                    customer_price_with_spp=round((product_kopecks + logistics_kopecks) / 100, 2),
                    product_kopecks=product_kopecks,
                    logistics_kopecks=logistics_kopecks,
                    price_fields_json=json.dumps(price, ensure_ascii=False, sort_keys=True),
                    sale_conditions=sale_conditions,
                )
            )
    return WbStorefrontResult(
        sizes=tuple(sizes),
        failed_nm_ids=tuple(str(value) for value in response.get("failed_nm_ids") or ()),
        errors=tuple(str(value) for value in response.get("errors") or ()),
    )


def seller_prices_from_payload(goods: list[dict]) -> tuple[WbSellerPrice, ...]:
    """Parse seller API prices before they enter the hourly service."""
    prices: list[WbSellerPrice] = []
    for product in goods:
        if not isinstance(product, dict):
            continue
        nm_id = str(product.get("nmID") or product.get("nmId") or "")
        if not nm_id.isdigit() or not isinstance(product.get("sizes"), list):
            continue
        for size in product["sizes"]:
            if not isinstance(size, dict) or size.get("discountedPrice") is None:
                continue
            try:
                size_id = int(size["sizeID"]) if size.get("sizeID") is not None else None
            except (TypeError, ValueError, OverflowError):
                size_id = None
            try:
                discounted_price = Decimal(str(size["discountedPrice"]))
            except (InvalidOperation, TypeError, ValueError):
                continue
            prices.append(
                WbSellerPrice(
                    nm_id=nm_id,
                    size_id=size_id,
                    tech_size_name=str(size.get("techSizeName") or "").strip(),
                    discounted_price=discounted_price,
                )
            )
    return tuple(prices)


class WbHourlyPriceService:
    def __init__(
        self,
        *,
        catalog_items: Callable[[str], tuple[WbCatalogItem, ...]],
        storefront_products: Callable[[tuple[str, ...]], WbStorefrontResult],
        seller_prices: Callable[[str, tuple[str, ...]], tuple[WbSellerPrice, ...]],
        wallet_discount: Callable[[], float | None],
        wallet_price: Callable[[float, float, int], float | None],
        writer: HourlyPriceWriter,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.catalog_items = catalog_items
        self.storefront_products = storefront_products
        self.seller_prices = seller_prices
        self.wallet_discount = wallet_discount
        self.wallet_price = wallet_price
        self.writer = writer
        self.clock = clock

    @staticmethod
    def _matching_size(article: str, sizes: list[WbStorefrontSize]) -> WbStorefrontSize | None:
        name = article.partition(" / ")[2].strip().casefold()
        if name:
            match = next((size for size in sizes if size.tech_size_name.casefold() == name), None)
            if match is not None:
                return match
        return sizes[0] if sizes and (not name or len(sizes) == 1) else None

    @staticmethod
    def _matching_seller_price(
        size: WbStorefrontSize, sellers: list[WbSellerPrice], storefront_size_count: int
    ) -> Decimal | None:
        size_id = size.size_id
        if size_id is not None:
            matches = [seller for seller in sellers if seller.size_id == size_id]
            if len(matches) == 1:
                return matches[0].discounted_price
            if any(seller.size_id is not None for seller in sellers):
                return None
        name = size.tech_size_name.casefold()
        if name:
            matches = [seller for seller in sellers if seller.tech_size_name.casefold() == name]
            if len(matches) == 1:
                return matches[0].discounted_price
        if storefront_size_count == 1 and len(sellers) == 1 and sellers[0].size_id is None:
            return sellers[0].discounted_price
        return None

    @staticmethod
    def _spp_percent(seller_price: Decimal | None, customer_price: float) -> float | None:
        if seller_price is None:
            return None
        try:
            customer = Decimal(str(customer_price))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if (
            not seller_price.is_finite()
            or not customer.is_finite()
            or seller_price <= 0
            or customer <= 0
            or customer > seller_price
        ):
            return None
        return float((seller_price - customer) / seller_price * 100)

    def collect(self, stores: tuple[str, ...]) -> WbHourlyCollection:
        started_at = monotonic()
        logger.info("Почасовые цены WB: начало сбора, магазинов=%s", len(stores))
        catalogs: dict[str, tuple[WbCatalogItem, ...]] = {}
        reports: list[WbHourlyStoreReport] = []
        for store in stores:
            try:
                catalogs[store] = self.catalog_items(store)
            except Exception as error:
                logger.warning("Почасовые цены WB: каталог недоступен, магазин=%s: %s", store, error)
                reports.append(WbHourlyStoreReport(store_slug=store, ok=False, rows=0, error=str(error)))
            else:
                logger.info(
                    "Почасовые цены WB: каталог загружен, магазин=%s, артикулов=%s",
                    store,
                    len(catalogs[store]),
                )
        nm_ids = tuple(
            sorted(
                {
                    nm_id
                    for items in catalogs.values()
                    for item in items
                    if (nm_id := item.article.partition(" / ")[0].strip()).isdigit()
                },
                key=int,
            )
        )
        if not nm_ids:
            logger.info("Почасовые цены WB: нет nmID для запроса витрины")
            reports.extend(
                WbHourlyStoreReport(store_slug=store, ok=True, rows=0, catalog_products=0)
                for store in catalogs
            )
            logger.info(
                "Почасовые цены WB: сбор завершён, магазинов=%s, сохранено=0, ошибок магазинов=%s, время=%.1f с",
                len(reports),
                sum(not report.ok for report in reports),
                monotonic() - started_at,
            )
            return WbHourlyCollection(tuple(reports))
        logger.info("Почасовые цены WB: запрашиваем витрину, уникальных nmID=%s", len(nm_ids))
        try:
            response = self.storefront_products(nm_ids)
        except Exception:
            logger.exception("Почасовые цены WB: запрос витрины завершился ошибкой")
            raise
        logger.info(
            "Почасовые цены WB: ответ витрины, размеров с ценой=%s, недоступных nmID=%s, ошибок=%s",
            len(response.sizes),
            len(response.failed_nm_ids),
            len(response.errors),
        )
        by_nm: dict[str, list[WbStorefrontSize]] = defaultdict(list)
        for size in response.sizes:
            by_nm[size.nm_id].append(size)
        failed = set(response.failed_nm_ids)
        try:
            discount = self.wallet_discount()
        except Exception as error:
            logger.warning("Почасовые цены WB: скидка Кошелька недоступна: %s", error)
            discount = None
        for store, items in catalogs.items():
            store_nm_ids = tuple(
                sorted(
                    {nm_id for item in items if (nm_id := item.article.partition(" / ")[0].strip()) in by_nm},
                    key=int,
                )
            )
            seller_by_nm: dict[str, list[WbSellerPrice]] = defaultdict(list)
            try:
                if store_nm_ids:
                    for seller_price in self.seller_prices(store, store_nm_ids):
                        seller_by_nm[seller_price.nm_id].append(seller_price)
            except Exception as error:
                logger.warning("Почасовые цены WB: цены продавца недоступны, магазин=%s: %s", store, error)
                seller_by_nm.clear()
            else:
                logger.info(
                    "Почасовые цены WB: цены продавца, магазин=%s, запрошено nmID=%s, получено размеров=%s",
                    store,
                    len(store_nm_ids),
                    sum(len(sizes) for sizes in seller_by_nm.values()),
                )
            observed_at = self.clock().astimezone(UTC)
            hour = observed_at.replace(minute=0, second=0, microsecond=0).isoformat()
            rows: list[WbHourlyPriceObservation] = []
            missing = 0
            for item in items:
                article = item.article.strip()
                nm_id = article.partition(" / ")[0].strip()
                if not nm_id.isdigit():
                    continue
                size = self._matching_size(article, by_nm.get(nm_id, []))
                if size is None:
                    missing += 1
                    continue
                spp = size.customer_price_with_spp
                seller_price = self._matching_seller_price(
                    size, seller_by_nm.get(nm_id, []), len(by_nm.get(nm_id, []))
                )
                conditions = size.sale_conditions
                rows.append(
                    WbHourlyPriceObservation(
                        store_slug=store,
                        article=article,
                        observed_hour=hour,
                        observed_at=observed_at.isoformat(),
                        nm_id=nm_id,
                        size_id=size.size_id,
                        tech_size_name=size.tech_size_name,
                        customer_price_with_spp=spp,
                        spp_percent=self._spp_percent(seller_price, spp),
                        customer_price_with_wallet=(
                            self.wallet_price(spp, discount, conditions) if discount is not None else None
                        ),
                        wallet_discount_percent=discount,
                        product_kopecks=size.product_kopecks,
                        logistics_kopecks=size.logistics_kopecks,
                        price_fields_json=size.price_fields_json,
                        sale_conditions=conditions,
                    )
                )
            try:
                saved = self.writer.upsert(rows)
            except Exception as error:
                logger.exception("Почасовые цены WB: запись не удалась, магазин=%s", store)
                reports.append(WbHourlyStoreReport(store_slug=store, ok=False, rows=0, error=str(error)))
                continue
            affected = tuple(sorted({item.article.partition(" / ")[0] for item in items} & failed))
            logger.info(
                "Почасовые цены WB: магазин=%s, сохранено=%s, с процентом СПП=%s, "
                "без цены витрины=%s, недоступных nmID=%s",
                store,
                saved,
                sum(row.spp_percent is not None for row in rows),
                missing,
                len(affected),
            )
            reports.append(
                WbHourlyStoreReport(
                    store_slug=store,
                    ok=missing == 0 and not affected,
                    rows=saved,
                    catalog_products=len(items),
                    missing_prices=missing,
                    failed_nm_ids=affected,
                    errors=response.errors if affected else (),
                )
            )
        logger.info(
            "Почасовые цены WB: сбор завершён, магазинов=%s, сохранено=%s, ошибок магазинов=%s, время=%.1f с",
            len(reports),
            sum(report.rows for report in reports),
            sum(not report.ok for report in reports),
            monotonic() - started_at,
        )
        return WbHourlyCollection(tuple(reports))
