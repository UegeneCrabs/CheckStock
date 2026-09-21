"""Temporary common FBY/FBS economics; original marketplace records stay separate."""

from collections import defaultdict

SCHEME = "COMMON"
SOURCE_SCHEMES = ("FBY", "FBS")


def settings(rows, article):
    if (article, SCHEME) in rows:
        return rows[article, SCHEME]
    legacy = [rows[article, scheme] for scheme in SOURCE_SCHEMES if (article, scheme) in rows]
    values = {}
    for row in sorted(legacy, key=lambda row: row.get("updated_at", "")):
        values.update(row["values"])
    # A first shared save creates its own revision, preserving both legacy rows.
    return {"revision": 0, "values": values}


def history(rows, article):
    grouped = defaultdict(dict)
    for row in rows:
        if row["article"] == article:
            grouped[row["day"]][row["scheme"]] = row["data"]
    result = []
    for day, models in sorted(grouped.items()):
        if SCHEME in models:
            result.append(models[SCHEME])
            continue
        # Never present one model's historical profit as a complete common day.
        if not all(scheme in models for scheme in SOURCE_SCHEMES):
            continue
        combined = {"day": day, "scheme": SCHEME}
        for key in ("profit", "purchase_value", "orders_count", "expected_buyouts", "advertising_spend"):
            values = [models[scheme].get(key) for scheme in SOURCE_SCHEMES]
            combined[key] = sum(values) if all(value is not None for value in values) else None
        if combined["orders_count"] and combined["expected_buyouts"] is not None:
            combined["inputs"] = {
                "buyout_percent": combined["expected_buyouts"] / combined["orders_count"] * 100
            }
        result.append(combined)
    return result
