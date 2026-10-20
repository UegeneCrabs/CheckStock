"""Storage allocation per sold unit and the agreed no-sales estimate."""

from app.economics.wb.standard_costs import number

NO_SALES_STORAGE_DAYS = 90


def storage_per_unit(row):
    """Return the calculation metadata and reasons without mutating the row."""
    sales = number(row.get("sales"))
    storage = number(row.get("storage"))
    daily_rate = number(row.get("storage_unit_day"))
    basis, days = "weekly_sales", None
    value = (
        storage / sales
        if storage is not None and sales and sales > 0 and row.get("storage_unit_day") is not None
        else None
    )
    if row.get("scheme") == "FBS":
        basis, value = "fbs_zero", 0.0
    elif row.get("scheme") == "FBW" and sales == 0:
        basis, days = "estimate_90_days", NO_SALES_STORAGE_DAYS
        value = daily_rate * days if daily_rate is not None and daily_rate >= 0 else None

    missing = []
    if value is None:
        if sales is None or sales <= 0:
            missing.append("Нет продаж за неделю: хранение на проданную штуку не определено")
        if storage is None or row.get("storage_unit_day") is None:
            missing.append(
                "Нет подтверждённых базовых начислений хранения WB; отсутствие строк не означает ноль"
            )
    return dict(basis=basis, days=days, daily_rate=daily_rate, value=value), missing
