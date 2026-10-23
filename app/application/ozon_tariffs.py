from collections.abc import Awaitable, Callable
from datetime import date

from app.core.constants import OZON_TARIFFS, OzonCalculatorCommissionMode, OzonPriceCalculatorField
from app.dto.ozon_price_calculator import OzonPriceCalculation, OzonPriceCalculationRequest, OzonPriceEdit
from app.dto.ozon_tariffs import OzonCategorySelection, OzonTariffCatalog
from app.economics.ozon_price_calculator import calculate
from app.ozon.tariff_normalizer import commission_percent, match_tariff


class OzonTariffService:
    def __init__(
        self,
        catalog: OzonTariffCatalog,
        category_reader: Callable[[str], Awaitable[OzonCategorySelection]],
        today: Callable[[], date],
    ) -> None:
        self.catalog = catalog
        self.category_reader = category_reader
        self.today = today
        self._items = {item.id: item for item in catalog.items}

    async def category(self, article: str) -> OzonCategorySelection:
        identity = await self.category_reader(article)
        return identity.model_copy(update={"tariff_id": match_tariff(identity.type_name, self.catalog.items)})

    def calculate(self, request: OzonPriceCalculationRequest) -> OzonPriceCalculation:
        tariff_id = request.tariff_id or request.product_tariff_id
        item = self._items.get(tariff_id) if tariff_id is not None else None
        if tariff_id is not None and (item is None or self.catalog.effective_from > self.today()):
            raise ValueError("Tariff unavailable")
        regular_percent = request.values.regular_commission_percent
        if request.initialize or regular_percent is None:
            if (
                request.values.seller_price is not None
                and request.values.seller_price > OZON_TARIFFS.second_price_limit
                and request.values.commission_mode == OzonCalculatorCommissionMode.AUTOMATIC
            ):
                regular_percent = request.values.commission_percent
        if item is not None and (request.tariff_id is not None or regular_percent is None):
            regular_percent = item.fbo_over_300
        mode = (
            OzonCalculatorCommissionMode.AUTOMATIC
            if request.initialize or request.tariff_id is not None
            else request.values.commission_mode
        )
        for edit in request.edits:
            mode = (
                OzonCalculatorCommissionMode.MANUAL
                if edit.field
                in (OzonPriceCalculatorField.COMMISSION_PERCENT, OzonPriceCalculatorField.COMMISSION_AMOUNT)
                else OzonCalculatorCommissionMode.AUTOMATIC
            )
        if request.tariff_id is not None:
            mode = OzonCalculatorCommissionMode.AUTOMATIC
        values = calculate(request)
        values = values.model_copy(
            update={"regular_commission_percent": regular_percent, "commission_mode": mode}
        )
        if mode == OzonCalculatorCommissionMode.MANUAL and request.tariff_id is None:
            return values
        price = values.seller_price
        percent = (
            None
            if price is None
            else commission_percent(item, price)
            if 0 < price <= OZON_TARIFFS.second_price_limit
            else regular_percent
        )
        return calculate(
            OzonPriceCalculationRequest(
                values=values,
                edits=(
                    OzonPriceEdit(
                        field=OzonPriceCalculatorField.COMMISSION_PERCENT,
                        value=percent,
                    ),
                ),
            )
        )
