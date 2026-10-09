"""Persist actual hourly WB storefront price observations."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime

from app.application.ports import HourlyPriceWriter


class WbHourlyPriceService:
    def __init__(
        self,
        *,
        catalog_items: Callable[[str], list[dict]],
        storefront_products: Callable[[tuple[str, ...]], dict],
        wallet_discount: Callable[[], float | None],
        wallet_price: Callable[[float, float, int], float | None],
        writer: HourlyPriceWriter,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.catalog_items = catalog_items
        self.storefront_products = storefront_products
        self.wallet_discount = wallet_discount
        self.wallet_price = wallet_price
        self.writer = writer
        self.clock = clock

    @staticmethod
    def _price_rows(products: list[dict]) -> dict[str, list[dict]]:
        by_nm: dict[str, list[dict]] = defaultdict(list)
        for product in products:
            if not isinstance(product, dict):
                continue
            nm_id = str(product.get("id") or product.get("nmID") or product.get("nmId") or "")
            if not nm_id.isdigit():
                continue
            sizes = product.get("sizes")
            if not isinstance(sizes, list):
                continue
            for size in sizes:
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
                by_nm[nm_id].append(
                    {
                        "size_id": size_id,
                        "tech_size_name": str(size.get("origName") or size.get("name") or "").strip(),
                        "customer_price_with_spp": round((product_kopecks + logistics_kopecks) / 100, 2),
                        "product_kopecks": product_kopecks,
                        "logistics_kopecks": logistics_kopecks,
                        "price_fields_json": json.dumps(price, ensure_ascii=False, sort_keys=True),
                        "sale_conditions": sale_conditions,
                    }
                )
        return by_nm

    @staticmethod
    def _matching_size(article: str, sizes: list[dict]) -> dict | None:
        name = article.partition(" / ")[2].strip().casefold()
        if name:
            match = next((size for size in sizes if size["tech_size_name"].casefold() == name), None)
            if match is not None:
                return match
        return sizes[0] if sizes and (not name or len(sizes) == 1) else None

    def collect(self, stores: tuple[str, ...]) -> dict[str, dict]:
        catalogs: dict[str, list[dict]] = {}
        reports: dict[str, dict] = {}
        for store in stores:
            try:
                catalogs[store] = self.catalog_items(store)
            except Exception as error:
                reports[store] = {"ok": False, "rows": 0, "error": str(error)}
        nm_ids = tuple(
            sorted(
                {
                    nm_id
                    for items in catalogs.values()
                    for item in items
                    if (nm_id := str(item.get("article") or "").partition(" / ")[0].strip()).isdigit()
                },
                key=int,
            )
        )
        if not nm_ids:
            reports.update({store: {"ok": True, "rows": 0, "catalog_products": 0} for store in catalogs})
            return reports
        response = self.storefront_products(nm_ids)
        by_nm = self._price_rows(list(response.get("products") or []))
        failed = set(map(str, response.get("failed_nm_ids") or []))
        errors = [str(error) for error in response.get("errors") or []]
        try:
            discount = self.wallet_discount()
        except Exception:
            discount = None
        observed_at = self.clock().astimezone(UTC)
        hour = observed_at.replace(minute=0, second=0, microsecond=0).isoformat()
        for store, items in catalogs.items():
            rows: list[dict] = []
            missing = 0
            for item in items:
                article = str(item.get("article") or "").strip()
                nm_id = article.partition(" / ")[0].strip()
                if not nm_id.isdigit():
                    continue
                size = self._matching_size(article, by_nm.get(nm_id, []))
                if size is None:
                    missing += 1
                    continue
                spp = size["customer_price_with_spp"]
                conditions = size["sale_conditions"]
                rows.append(
                    {
                        "store_slug": store,
                        "article": article,
                        "observed_hour": hour,
                        "observed_at": observed_at.isoformat(),
                        "nm_id": nm_id,
                        **size,
                        "customer_price_with_wallet": (
                            self.wallet_price(spp, discount, conditions) if discount is not None else None
                        ),
                        "wallet_discount_percent": discount,
                    }
                )
            try:
                saved = self.writer.upsert(rows)
            except Exception as error:
                reports[store] = {"ok": False, "rows": 0, "error": str(error)}
                continue
            affected = sorted({str(item.get("article") or "").partition(" / ")[0] for item in items} & failed)
            reports[store] = {
                "ok": missing == 0 and not affected,
                "rows": saved,
                "catalog_products": len(items),
                "missing_prices": missing,
                "failed_nm_ids": affected,
                "errors": errors if affected else [],
            }
        return reports
