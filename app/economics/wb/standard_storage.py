"""Storage allocation per sold unit and the agreed no-sales estimate."""

from app.dto.wb_standard_costs import ProductEconomics, ReportRow, StorageCalculation
from app.economics.wb.standard_costs import number

NO_SALES_STORAGE_DAYS = 90
MISSING_STORAGE_WARNING = "Хранение принято за 0 ₽: API WB не вернул начисления за период."


def storage_per_unit(row: ReportRow, economics: ProductEconomics) -> tuple[StorageCalculation, list[str]]:
    """Return the calculation metadata and reasons without mutating the row."""
    orders = number(economics.net_orders_count)
    orders_complete = bool(economics.turnover_coverage and economics.turnover_coverage.complete)
    storage = number(row.storage)
    daily_rate = number(row.storage_unit_day)
    basis, days = "funnel_net_orders", None
    value = (
        storage / orders
        if storage is not None
        and orders is not None
        and orders > 0
        and orders_complete
        and row.storage_unit_day is not None
        else None
    )
    if row.scheme == "FBS":
        basis, value = "fbs_zero", 0.0
    elif (
        row.scheme == "FBW"
        and storage is None
        and "В отчёте WB нет базовых строк хранения товара за неделю" in (row.missing or [])
        and not {
            "Начисление хранения без количества товара",
            "Нет подтверждённой суммы хранения до скидок",
            "Не поддержан тип хранения или отсутствует количество",
        }.intersection(row.missing or [])
    ):
        basis, value = "missing_api_zero", 0.0
    elif row.scheme == "FBW" and orders_complete and orders == 0:
        basis, days = "estimate_90_days", NO_SALES_STORAGE_DAYS
        value = daily_rate * days if daily_rate is not None and daily_rate >= 0 else None

    missing = []
    if value is None:
        if orders is None or not orders_complete:
            missing.append("Нет полных данных воронки «Заказы − отмены» за период")
        elif orders < 0:
            missing.append("Заказы − отмены меньше нуля: хранение на штуку не определено")
        if storage is None or row.storage_unit_day is None:
            missing.append(
                "Нет подтверждённых базовых начислений хранения WB; отсутствие строк не означает ноль"
            )
    return StorageCalculation(
        basis=basis,
        net_orders_count=orders,
        orders_complete=orders_complete,
        days=days,
        daily_rate=daily_rate,
        value=value,
        warning=MISSING_STORAGE_WARNING if basis == "missing_api_zero" else None,
    ), missing
