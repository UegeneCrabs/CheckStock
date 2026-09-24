"""Authoritative Decimal calculation for the WB browser calculator scenario."""

from decimal import Decimal, InvalidOperation

from app.economics.wb.calculations import money


def _number(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def calculate(
    values: dict[str, float | None],
    *,
    tax_system: str,
    turnover_days: float | None,
    acquiring_percent: float | None,
    purchase_cost: float | None,
) -> dict[str, float | None]:
    """Use unrounded costs; round only the displayed margin and ROI."""

    def value(key: str, fallback: Decimal | None = None) -> Decimal | None:
        candidate = _number(values.get(key))
        return candidate if candidate is not None else fallback

    def percent_cost(base: Decimal | None, percent: Decimal | None) -> Decimal | None:
        return base * percent / 100 if base is not None and percent is not None else None

    retail = value("retail")
    retail = max(retail, Decimal(0)) if retail is not None else None
    client = value("client", retail)
    client = max(client, Decimal(0)) if client is not None else None
    acquiring_rate = value("acquiringPercent", _number(acquiring_percent))
    acquiring = value("acquiringRub", percent_cost(retail, acquiring_rate))
    commission = value("commissionRub", percent_cost(retail, value("commission")))
    team = value("teamRub", percent_cost(retail, value("team")))
    vat_rate = value("vat")
    calculated_vat = (
        client * vat_rate / (100 + vat_rate)
        if client is not None and vat_rate is not None and vat_rate != -100
        else None
    )
    vat = value("vatRub", calculated_vat)
    secondary_rate = value("osno" if tax_system == "osno" else "usn")
    secondary_base = client if tax_system == "osno" else client - vat if client is not None and vat is not None else None
    secondary = value("secondaryTaxRub", percent_cost(secondary_base, secondary_rate))
    purchase = value("purchase", _number(purchase_cost))
    storage_rate = value("storage")
    storage_days = _number(turnover_days)
    storage = value(
        "storageTotal",
        storage_rate * storage_days if storage_rate is not None and storage_days is not None else None,
    )
    expenses = [
        acquiring,
        value("logistics"),
        storage,
        commission,
        value("advertisingRub"),
        purchase,
        value("fulfillment"),
        team,
        vat,
        secondary,
    ]
    if retail is None or any(expense is None for expense in expenses):
        return {"margin": None, "roi": None}
    margin = retail - sum(expenses)
    return {
        "margin": money(margin),
        "roi": money(margin / purchase * 100) if purchase and purchase > 0 else None,
    }
