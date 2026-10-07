"""Financial read models and import orchestration. Reads never contact the provider."""

import hashlib
import json
from calendar import monthrange
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from app.core.domain import MOSCOW_TIMEZONE
from app.finance.calculation import LABELS, SOURCES, VERSION, calculate, contributions, days, money


class FinancePort(Protocol):
    def read(self, stores, start, end): ...
    def connections(self, stores): ...
    def publish(self, connection, batches, actor): ...
    def failed(self, connection_id, month, sources, error, state="error"): ...
    def saved_batches(self, identifier, month): ...
    def costs(self, store): ...


def today():
    return datetime.now(MOSCOW_TIMEZONE).date()


def period(start=None, end=None):
    end = end or today() - timedelta(days=1)
    start = start or end - timedelta(days=6)
    if start > end or end > today() or (end - start).days >= 366:
        raise ValueError("Нужен период от 1 до 366 дней включительно без будущих дат")
    return start, end


def months(start, end):
    current = start.replace(day=1)
    available = today() if end == today() else today() - timedelta(days=1)
    while current <= end:
        last = current.replace(day=monthrange(current.year, current.month)[1])
        yield current, min(last, available)
        current = last + timedelta(days=1)


class FinanceService:
    def __init__(self, repository: FinancePort):
        self.repository = repository

    def _coverage(self, data, stores, start, end):
        coverage = {s: [] for s in (*SOURCES, "income")}
        snaps = {(s["connection_id"], s["source"], s["start"]): s for s in data["snapshots"]}
        for store in stores:
            connections = [c for c in data["connections"] if c["store_slug"] == store]
            for day in days(start, end):
                active = [c for c in connections if c["effective_from"] <= day <= (c["effective_to"] or end)]
                for source in SOURCES:
                    if not active:
                        coverage[source].append(f"{store}: не настроено подключение, {day}")
                        if source == "payments":
                            coverage["income"].append(f"{store}: нет источника прочих начислений, {day}")
                    for conn in active:
                        snap = snaps.get((conn["id"], source, day.replace(day=1)))
                        if not snap or snap["end"] < day:
                            coverage[source].append(
                                f"{store}, кабинет {conn['business_id']}: нет {source}, {day}"
                            )
                            if source == "payments":
                                coverage["income"].append(f"{store}: нет источника прочих начислений, {day}")
                        elif snap["issues"]:
                            coverage[source].extend(f"{store}, {source}: {issue}" for issue in snap["issues"])
                        if snap and source == "payments":
                            coverage["income"].extend(f"{store}: {issue}" for issue in snap["income_issues"])
        if not stores:
            for source in coverage:
                coverage[source].append("Нет доступных магазинов")
        return {key: list(dict.fromkeys(value)) for key, value in coverage.items()}

    def report(self, stores, start, end, *, running=False):
        start, end = period(start, end)
        data = self.repository.read(tuple(stores), start, end)
        version = hashlib.sha256(
            json.dumps(
                {
                    "stores": sorted(stores),
                    "from": str(start),
                    "to": str(end),
                    "heads": sorted(s["id"] for s in data["snapshots"]),
                    "formula": VERSION,
                    "connections": [(c["id"], str(c["effective_to"])) for c in data["connections"]],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:24]
        events = [row["event"] for row in data["events"]]
        current_month = today().replace(day=1)
        totals = calculate(events, self._coverage(data, stores, start, end), preliminary=end >= current_month)
        daily = [
            {
                "day": day.isoformat(),
                "metrics": calculate(
                    [e for e in events if e.day == day],
                    self._coverage(data, stores, day, day),
                    preliminary=day >= current_month,
                ),
            }
            for day in days(start, end)
        ]
        statuses = []
        for conn in data["connections"]:
            for source in SOURCES:
                snapshots = [
                    s for s in data["snapshots"] if s["connection_id"] == conn["id"] and s["source"] == source
                ]
                attempts = [
                    a for a in data["attempts"] if a["connection_id"] == conn["id"] and a["source"] == source
                ]
                errors = [
                    dict(
                        month=str(a["month"]),
                        message=a["error"] or "Загрузка прервана; повторите задание",
                        attempted_at=a["attempted_at"],
                    )
                    for a in attempts
                    if a["error"] or (a["state"] == "running" and not running)
                ]
                stamps = [s["captured_at"] for s in snapshots]
                statuses.append(
                    {
                        "store_slug": conn["store_slug"],
                        "business_id": conn["business_id"],
                        "source": source,
                        "last_success": max(stamps, default=None),
                        "oldest_success": min(stamps, default=None),
                        "stale": bool(errors),
                        "errors": errors,
                        "running": running and any(a["state"] == "running" for a in attempts),
                        "coverage": [
                            {
                                "from": str(s["start"]),
                                "to": str(s["end"]),
                                "issues": [*s["issues"], *s["income_issues"]],
                            }
                            for s in snapshots
                        ],
                    }
                )
        return {
            "ok": True,
            "stores": list(stores),
            "date_from": str(start),
            "date_to": str(end),
            "version": version,
            "formula_version": VERSION,
            "metrics": totals,
            "daily": daily,
            "status": statuses,
            "empty_scope": not stores,
            "preliminary": end >= current_month,
            "stale": any(s["stale"] for s in statuses),
            "_events": data["events"],
        }

    def details(self, report, *, version, metric, page=1, page_size=50, day=None, kind=None, category=None):
        if report["version"] != version:
            raise LookupError("Данные обновились. Обновите сводку и откройте детали заново")
        if metric not in LABELS or metric == "margin_percent":
            raise ValueError("Выберите денежный показатель или количество; процент рассчитан из итогов")
        rows = []
        for row in report["_events"]:
            event = row["event"]
            if (
                (day and str(event.day) != day)
                or (kind and event.kind != kind)
                or (category and event.category != category)
            ):
                continue
            values = contributions(event)
            if metric not in values:
                continue
            value = values[metric]
            rows.append(
                {
                    **{k: v for k, v in row.items() if k != "event"},
                    **event.model_dump(mode="json"),
                    "contribution": str(value) if value is not None else None,
                    "correction": event.kind == "return" or (event.amount is not None and event.amount < 0),
                }
            )
        rows.sort(key=lambda r: (r["day"], r["store_slug"], r["business_id"], r["key"]))
        start = (page - 1) * page_size
        return {
            "version": version,
            "metric": metric,
            "rows": rows[start : start + page_size],
            "total_count": len(rows),
            "page": page,
            "page_size": page_size,
            "known_sum": money(
                sum((Decimal(r["contribution"]) for r in rows if r["contribution"] is not None), Decimal(0))
            ),
            "unknown_count": sum(r["contribution"] is None for r in rows),
        }

    def price_batches(self, connection, batches, *, recalculate=False):
        """Persist a sale's cost; restore that exact unit cost on its financial return."""
        # Monthly archives can contain sales belonging to a previous store binding.
        # They must not establish a purchase cost in the new store's ledger.
        batches = [
            b.model_copy(
                update={
                    "events": tuple(
                        e
                        for e in b.events
                        if connection["effective_from"] <= e.day <= (connection["effective_to"] or b.end)
                    )
                }
            )
            for b in batches
        ]
        realized = [b for b in batches if b.source == "realization"]
        if not realized:
            return batches
        end = max(b.end for b in realized)
        history = self.repository.read((connection["store_slug"],), connection["effective_from"], end)
        prior = [r["event"] for r in history["events"] if r["business_id"] == connection["business_id"]]
        costs = self.repository.costs(connection["store_slug"])

        def identity(e):
            return e.campaign_id, e.order_id, e.article, e.day

        old_prices = defaultdict(set)
        for e in prior:
            if e.kind == "sale" and e.cost is not None:
                old_prices[identity(e)].add(e.cost / e.quantity)
        costs_by_article = defaultdict(list)
        for c in sorted(costs, key=lambda c: (c["origin"] == "manual", c["effective_from"], c["id"])):
            costs_by_article[c["article"]].append(c)

        def unit_price(event):
            matching = [
                c
                for c in costs_by_article[event.article]
                if c["effective_from"] <= event.day <= (c["effective_to"] or event.day)
            ]
            old = old_prices[identity(event)]
            if old and not recalculate:
                return (
                    (next(iter(old)), "сохранённая цена продажи")
                    if len(old) == 1
                    else (None, "неоднозначная цена продажи")
                )
            if matching:
                c = matching[-1]
                return Decimal(c["price"]), f"{c['origin']}:{c['id']}"
            return None, "нет закупочной цены на дату реализации"

        sales = []
        for b in realized:
            for e in b.events:
                if e.kind == "sale":
                    price, origin = unit_price(e)
                    sales.append(
                        e.model_copy(
                            update={
                                "cost": price * e.quantity if price is not None else None,
                                "cost_origin": origin,
                            }
                        )
                    )
        replaced_months = {b.start for b in realized}
        available = sales + [
            e for e in prior if e.kind == "sale" and e.day.replace(day=1) not in replaced_months
        ]
        sales_by_item = defaultdict(list)
        for e in available:
            sales_by_item[(e.campaign_id, e.order_id, e.article)].append(e)
        priced = {}
        for b in realized:
            events = []
            sale_map = {s.key: s for s in sales if s.day.replace(day=1) == b.start}
            for e in b.events:
                if e.kind == "sale":
                    events.append(sale_map[e.key])
                elif e.kind == "return":
                    linked = [
                        s
                        for s in sales_by_item[(e.campaign_id, e.order_id, e.article)]
                        if (not e.original_day or s.day == e.original_day) and s.day <= e.day
                    ]
                    prices = {s.cost / s.quantity for s in linked if s.cost is not None}
                    valid = (
                        linked
                        and all(s.cost is not None for s in linked)
                        and len(prices) == 1
                        and e.quantity <= sum(s.quantity for s in linked)
                    )
                    events.append(
                        e.model_copy(
                            update={
                                "cost": next(iter(prices)) * e.quantity if valid else None,
                                "cost_origin": "цена исходной продажи"
                                if valid
                                else "не найдена однозначная закупка исходной продажи",
                            }
                        )
                    )
                else:
                    events.append(e)
            priced[b.start] = b.model_copy(update={"events": tuple(events)})
        return [priced[b.start] if b.source == "realization" else b for b in batches]

    def synchronize(self, stores, start, end, provider, actor, *, mode="import", scheduled=False):
        start, end = period(start, end)
        connections = self.repository.connections(tuple(stores))
        results = []
        for conn in connections:
            if (
                (not conn["active"] and scheduled)
                or conn["effective_from"] > end
                or (conn["effective_to"] and conn["effective_to"] < start)
            ):
                continue
            for month, last in months(
                max(start, conn["effective_from"]), min(end, conn["effective_to"] or end)
            ):
                for group in (("realization", "services"), ("orders",), ("payments",)):
                    try:
                        self.repository.failed(conn["id"], month, group, "", state="running")
                        if mode == "recalculate":
                            saved = {b.source: b for b in self.repository.saved_batches(conn["id"], month)}
                            if not all(source in saved for source in group):
                                raise ValueError("Нет сохранённых источников для пересчёта")
                            batches = [provider.reparse(conn, saved[source]) for source in group]
                        else:
                            batches = [provider.load(conn, source, month, last) for source in group]
                        batches = self.price_batches(conn, batches, recalculate=mode == "recalculate")
                        self.repository.publish(conn, batches, actor)
                        if mode != "recalculate":
                            provider.published(conn, batches)
                        results.append(
                            {
                                "store": conn["store_slug"],
                                "business_id": conn["business_id"],
                                "month": str(month),
                                "sources": group,
                                "ok": True,
                            }
                        )
                    except Exception as error:
                        # Provider errors can contain URLs, headers or PII. Only vetted messages are public.
                        message = (
                            getattr(error, "public_message", None)
                            or f"Ошибка {type(error).__name__}; прежний снимок сохранён"
                        )
                        self.repository.failed(conn["id"], month, group, message)
                        results.append(
                            {
                                "store": conn["store_slug"],
                                "business_id": conn["business_id"],
                                "month": str(month),
                                "sources": group,
                                "ok": False,
                                "error": message,
                            }
                        )
        if not results:
            return {
                "ok": False,
                "error": "Нет подходящих финансовых подключений для выбранного периода",
                "results": [],
            }
        return {"ok": all(r["ok"] for r in results), "results": results}
