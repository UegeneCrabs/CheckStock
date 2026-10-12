"""Economic demand excludes cancellations; source counters remain gross."""


def net_count(row):
    return max(int(row.get("orders_count") or 0) - int(row.get("cancel_count") or 0), 0)


def net_amount(row):
    return max(float(row.get("orders_amount") or 0) - float(row.get("cancel_amount") or 0), 0.0)


def drr_percent(spend, expected_amount):
    if spend is None or expected_amount is None:
        return None
    if expected_amount > 0:
        return round(spend / expected_amount * 100, 2)
    return 0.0 if spend == 0 else None
