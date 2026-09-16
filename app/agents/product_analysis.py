"""Pure decisions over complete server-side snapshots; unknown is never zero."""

import math
from datetime import timedelta


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def manager_key(value):
    return tuple(sorted(str(value or "").casefold().replace("ё", "е").split()))


def covered_articles(rows, start, end, fields):
    days = {(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)}
    observed = {}
    for row in rows:
        if all(number(row.get(f)) is not None for f in fields):
            observed.setdefault(str(row.get("article") or row.get("nm_id")), set()).add(
                str(row.get("day"))[:10]
            )
    return {article for article, known in observed.items() if days <= known}


def stock_evidence(row, fulfillment=None):
    if row is None:
        return None, None
    keys = [
        k
        for k in row
        if k.endswith("_stock") and (not fulfillment or k == "fbs_stock" or k.startswith("fbs_"))
    ]
    values = [number(row.get(k)) for k in ["ff_available", *keys]]
    known = sum(v for v in values if v is not None)
    complete = bool(keys) and all(v is not None for v in values)
    return (known > 0 if complete or known > 0 else None), (known if complete else None)


def evaluate(
    scenario,
    product,
    previous,
    stock,
    tag,
    campaigns,
    *,
    orders_known,
    previous_orders_known,
    impressions_known,
    previous_impressions_known,
    drr_max,
    ctr_below,
    days,
    fulfillment=None,
):
    """Return match/no-match/unknown and reviewable metrics for one product."""
    metrics = {}
    reasons = []

    def unknown(reason):
        reasons.append(reason)
        return "unknown", metrics, reasons

    stock_positive, quantity = stock_evidence(stock, fulfillment)
    if scenario in {"stock_without_orders", "stock_impressions_drop"}:
        metrics["available_stock"] = quantity
        metrics["stock_presence_confirmed"] = stock_positive
        metrics["stock_observed_at"] = (stock or {}).get("mp_updated_at")
        if stock_positive is False:
            return "no_match", metrics, reasons
        if stock_positive is None:
            return unknown("stock_unknown")
    if scenario == "stock_without_orders":
        actual = number(product.get("orders_count"))
        if not orders_known or actual is None:
            return unknown("orders_period_incomplete")
        metrics["orders_count"] = actual
        matched = actual == 0
    elif scenario == "turnover_drop":
        current, old = number(product.get("orders_amount")), number((previous or {}).get("orders_amount"))
        if not orders_known or not previous_orders_known or current is None or old is None:
            return unknown("orders_comparison_incomplete")
        metrics.update(
            previous_orders_amount=old,
            turnover_change_rub=round(current - old, 2),
            turnover_change_percent=round((current - old) / old * 100, 2) if old > 0 else None,
        )
        matched = current < old
    elif scenario == "drr_negative_roi":
        drr, roi = number(product.get("drr")), number(product.get("roi"))
        metrics.update(drr=drr, roi=roi)
        if drr is not None and not 0 <= drr <= drr_max:
            return "no_match", metrics, reasons
        if roi is not None and roi >= 0:
            return "no_match", metrics, reasons
        if drr is None or roi is None or product.get("margin_complete") is not True:
            return unknown("economics_incomplete")
        matched = True
    elif scenario == "stock_impressions_drop":
        current, old = number(product.get("impressions")), number((previous or {}).get("impressions"))
        if not impressions_known or not previous_impressions_known or current is None or old is None:
            return unknown("advertising_comparison_incomplete")
        metrics.update(impressions=current, previous_impressions=old, impressions_change=current - old)
        matched = current < old
    elif scenario == "active_low_ctr":
        if campaigns is None:
            return unknown("campaign_snapshot_unavailable")
        active = [r for r in campaigns if r.get("is_active") is True]
        low = [r for r in active if number(r.get("ctr_percent")) is not None and r["ctr_percent"] < ctr_below]
        metrics["campaigns"] = [
            {
                k: r.get(k)
                for k in ("campaign_id", "ctr_percent", "impressions", "clicks", "spend", "updated_at")
            }
            for r in low
        ]
        if not low and any(number(r.get("ctr_percent")) is None for r in active):
            return unknown("campaign_ctr_unknown")
        matched = bool(low)
    else:
        goal = number((tag or {}).get("goal_week"))
        if goal is None:
            return unknown("weekly_goal_unknown")
        if goal <= 0:
            return "no_match", metrics, reasons
        actual = number(product.get("orders_count"))
        if not orders_known or actual is None:
            return unknown("orders_period_incomplete")
        expected = goal / 7 * days
        metrics.update(
            goal_week=goal,
            goal_for_period=round(expected, 2),
            orders_count=actual,
            shortfall=round(max(0, expected - actual), 2),
        )
        matched = actual < expected
    return "match" if matched else "no_match", metrics, reasons
