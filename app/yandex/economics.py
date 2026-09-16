"""Persistent YM economics. Reads never call marketplace APIs or write snapshots."""

from datetime import datetime, timedelta

from app.core.domain import MOSCOW_TIMEZONE
from app.repositories import unit_economics_yandex as metrics
from app.repositories import yandex_economics as repository
from app.repositories import yandex_source_values, yandex_storefront
from app.yandex.category_commissions import SOURCE as CATEGORY_COMMISSION_SOURCE
from app.yandex.category_commissions import commission_value
from app.yandex.economics_advertising import apply_calculator_drr, product_drr, weekly_history
from app.yandex.economics_calculation import (
    VERSION,
    aggregate,
    break_even_prices,
    calculate,
    resolve,
    sheet_logistics,
)
from app.yandex.price_calculation import discounted_price


def context(store):
    return {
        "sources": repository.sources(store),
        "settings": repository.all_settings(store),
        "source_1c": yandex_source_values.get_values(store),
        "prices": yandex_storefront.get_prices(store),
        "snapshots": metrics.get_snapshots(store),
        "buyout_settings": metrics.get_buyout_settings(store),
    }


def effective(store, article, scheme, *, scenario=None, state_cache=None, estimate_tariff=False):
    """Current quotes must match; planning can keep the last compatible saved tariff."""
    cache = state_cache if state_cache is not None else context(store)
    saved_sources = cache["sources"]
    seed = saved_sources.get((article, "initial:" + scheme), {})
    catalog = saved_sources.get((article, "catalog"), {})
    category = saved_sources.get((article, "category"), {})
    category_values = category.get("values", {})
    commission = saved_sources.get((article, CATEGORY_COMMISSION_SOURCE + scheme), {}).get("values", {})
    cabinet = cache["settings"].get(("", scheme), {"revision": 0, "values": {}})
    product = cache["settings"].get((article, scheme), {"revision": 0, "values": {}})
    source_1c = cache["source_1c"].get(article, {})
    base = {
        "purchase_price": source_1c.get("purchase_price"),
        "fulfillment_cost": source_1c.get("fulfillment_cost"),
        "other_percent": source_1c.get("team_commission_percent"),
    }
    prices = cache["prices"].get(article, {})
    pricing = yandex_storefront.resolved_prices(prices)
    price_layers = [(pricing["origins"][key], {key: pricing[key]}) for key in ("seller_price", "buyer_price")]
    buyout_settings = cache["buyout_settings"]
    snap = cache["snapshots"].get("buyout", {})
    if snap.get("period_from") and snap.get("period_to"):
        from datetime import date

        if (
            date.fromisoformat(snap["period_to"]) - date.fromisoformat(snap["period_from"])
        ).days + 1 != buyout_settings["buyout_period_days"]:
            snap = {}
    buyouts = {str(row["article"]): row for row in snap.get("data") or []}
    buyout = buyouts.get(article, {}).get("buyout_percent")
    if not buyout:
        buyout = buyout_settings.get("default_buyout_percent")
    layers = [
        ("Начальные данные 1С", base),
        ("Перенесено из листа", seed.get("values", {})),
        ("API: каталог", catalog.get("values", {})),
        *price_layers,
        ("API: выкуп", {"buyout_percent": buyout}),
        ("Настройки кабинета", cabinet["values"]),
        ("Изменено на сайте", product["values"]),
    ]
    if category.get("updated_at", "") >= catalog.get("updated_at", ""):
        layers.insert(
            3, ("API: категория", {key: category_values.get(key) for key in ("category_id", "category_name")})
        )
    values, _ = resolve(*layers, ("Сценарий", scenario or {}))
    reference_percent = commission_value(commission, values)
    if reference_percent is not None:
        layers.insert(-2, ("API: комиссия категории за неделю", {"commission_percent": reference_percent}))
    tariff = saved_sources.get((article, "tariff:" + scheme), {})
    tariff_data = tariff.get("values", {})

    tariff_fresh = yandex_storefront.fresh(tariff.get("updated_at"))
    tariff_valid = tariff_data.get("signature") == tariff_signature(values, scheme) and tariff_fresh
    estimated_tariff = (
        estimate_tariff
        and not tariff_valid
        and isinstance(tariff_data.get("signature"), dict)
        and {key: value for key, value in tariff_data["signature"].items() if key != "seller_price"}
        == {key: value for key, value in tariff_signature(values, scheme).items() if key != "seller_price"}
    )
    if tariff_valid or estimated_tariff:
        origin = "Последний загруженный тариф API" if estimated_tariff else "API: тариф"
        components = tariff_data.get("components", {})
        # A saved quote is a planning fallback, not a replacement for a current category rate.
        if estimated_tariff and reference_percent is not None:
            components = {key: value for key, value in components.items() if key != "commission_percent"}
        layers.insert(-2, (origin, components))
    values, origins = resolve(*layers, ("Сценарий", scenario or {}))
    apply_sheet_logistics(values, origins, scenario=scenario)
    if (scenario or {}).get("seller_price") is not None and (scenario or {}).get("buyer_price") is None:
        values["buyer_price"] = discounted_price(values["seller_price"], pricing.get("spp_percent"))
        origins["buyer_price"] = (
            "Сценарий: по последнему СПП" if values["buyer_price"] is not None else "Нет сохранённого СПП"
        )
    if any(values.get(key) != pricing[key] for key in ("seller_price", "buyer_price")):
        pricing = {
            **pricing,
            "seller_price": values.get("seller_price"),
            "buyer_price": values.get("buyer_price"),
            "buyer_estimated": values.get("buyer_price") is not None
            and origins.get("buyer_price") == "Сценарий: по последнему СПП",
            "pay_price": discounted_price(values.get("buyer_price"), pricing.get("pay_discount_percent")),
            "pay_estimated": True,
            "origins": {
                **pricing["origins"],
                "buyer_price": origins.get("buyer_price"),
                "pay_price": "Сценарий: по последней скидке Пэй",
            },
        }
    if (scenario or {}).get("pay_price") is not None:
        pricing = {
            **pricing,
            "pay_price": scenario["pay_price"],
            "pay_estimated": False,
            "origins": {**pricing["origins"], "pay_price": "Сценарий"},
        }
    values["pay_price"] = pricing["pay_price"]
    origins["pay_price"] = pricing["origins"]["pay_price"]
    return {
        "values": values,
        "origins": origins,
        "revision": product["revision"],
        "overrides": {key: value for key, value in product["values"].items() if key != "tax_base"},
        "cabinet_revision": cabinet["revision"],
        "pricing": pricing,
        "category": category_values
        if category_values.get("category_id") == values.get("category_id")
        else {},
        "category_commission": {**commission, "valid": reference_percent is not None},
        "tariff": {
            "valid": tariff_valid,
            "approximate": bool(estimated_tariff),
            "stale": bool(estimated_tariff and not tariff_fresh),
            "updated_at": tariff.get("updated_at"),
            "services": tariff_data.get("services", []),
        },
    }


def apply_sheet_logistics(values, origins, *, scenario=None):
    derived = sheet_logistics(values, scenario=scenario)
    values.update(derived)
    origins.update(
        volume_l="Габариты упаковки: длина × ширина × высота / 1000",
        return_middle_mile="По формуле таблицы: средняя миля от объёма",
        return_cost="По формуле таблицы: средняя миля + 15 ₽",
        storage_per_day="По формуле таблицы: объём × ставка по оборачиваемости",
        storage_days="По формуле таблицы: 30 дней",
    )
    for key in derived:
        if (scenario or {}).get(key) is not None:
            origins[key] = "Сценарий"


def tariff_signature(values, scheme):
    keys = (
        "seller_price",
        "category_id",
        "length",
        "width",
        "height",
        "weight",
        "campaign_id",
        "frequency",
        "payment_delay_weeks",
    )
    return {**{key: values.get(key) for key in keys}, "scheme": scheme}


def checked_today(timestamp, today):
    try:
        return datetime.fromisoformat(timestamp).astimezone(
            MOSCOW_TIMEZONE
        ).date() == today and yandex_storefront.fresh(timestamp)
    except (TypeError, ValueError):
        return False


def current_inputs(store, article, scheme, *, today, scenario=None, state_cache=None):
    """Today's metrics and standing costs, with explicit price estimates when needed.

    A price scenario is never a current marketplace observation. A stored tariff must have been quoted today for these observed prices."""
    cache = state_cache if state_cache is not None else context(store)

    scenario = {
        key: value
        for key, value in (scenario or {}).items()
        if key
        not in {
            "seller_price",
            "buyer_price",
            "pay_price",
            "advertising_mode",
            "plan_drr",
            "advertising_spend",
        }
    }
    state = effective(store, article, scheme, scenario=scenario, state_cache=cache)
    values, origins = state["values"], state["origins"]
    pricing = yandex_storefront.resolved_prices(cache["prices"].get(article, {}))
    state["pricing"] = pricing
    for field in ("seller_price", "buyer_price", "pay_price"):
        values[field] = pricing[field]
        origins[field] = pricing["origins"][field]
    values["advertising_mode"] = "actual"
    origins["advertising_mode"] = "Реклама и заказы за сегодня"

    tariff = cache["sources"].get((article, "tariff:" + scheme), {})
    quote = tariff.get("values", {})
    tariff_valid = checked_today(tariff.get("updated_at"), today) and quote.get(
        "signature"
    ) == tariff_signature(values, scheme)
    state["tariff"]["valid"] = tariff_valid
    reference_percent = commission_value(state["category_commission"], values)
    state["category_commission"]["valid"] = reference_percent is not None
    fields = ("commission_percent", "payment_acceptance", "payment_transfer_percent", "delivery_cost")
    for field in fields:
        if origins.get(field) in {"Изменено на сайте", "Настройки кабинета", "Сценарий"}:
            continue
        if field == "commission_percent" and not tariff_valid and reference_percent is not None:
            values[field] = reference_percent
            origins[field] = "API: комиссия категории за неделю"
            continue
        values[field] = quote.get("components", {}).get(field) if tariff_valid else None
        origins[field] = "API: тариф за сегодня" if tariff_valid else "Нет тарифа за сегодня"
    if origins.get("tariff_extra") not in {"Изменено на сайте", "Настройки кабинета", "Сценарий"}:
        values["tariff_extra"] = quote.get("components", {}).get("tariff_extra", 0) if tariff_valid else 0
    return state


def allocate_today(ads, orders, article, scheme):
    rows = [row for row in orders if row["article"] == article]
    if any("schemes" not in row for row in rows):
        return None, None
    spend = sum(float(row.get("spend") or 0) for row in ads if row["article"] == article)
    total = sum(int(row.get("orders_count") or 0) for row in rows)
    quantity = sum(int(row.get("schemes", {}).get(scheme, {}).get("orders_count", 0)) for row in rows)
    return (spend * quantity / total if total else spend if scheme == "FBY" else 0), quantity


def current_ads(store, article, today, scheme="FBY"):
    key = today.isoformat()
    ads, ads_days = metrics.get_history(store, "advertising", key, key)
    orders, order_days = metrics.get_history(store, "orders", key, key)
    if key not in ads_days or key not in order_days:
        return None, None
    return allocate_today(ads, orders, article, scheme)


def detail(
    store,
    article,
    scheme="FBY",
    *,
    today=None,
    scenario=None,
    state_cache=None,
    advertising=None,
    include_history=True,
    mode="current",
):
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    cache = state_cache if state_cache is not None else context(store)
    calculator = effective(store, article, scheme, scenario=scenario, state_cache=cache, estimate_tariff=True)
    if cache.get("advertising_week", {}).get("period_to") != (today - timedelta(days=1)).isoformat():
        cache["advertising_week"] = weekly_history(store, today)
    weekly = product_drr(cache["advertising_week"], article, calculator["values"].get("buyout_percent"))
    apply_calculator_drr(calculator["values"], calculator["origins"], weekly, scenario)
    config = (
        current_inputs(store, article, scheme, today=today, scenario=scenario, state_cache=cache)
        if mode == "current"
        else calculator
    )
    spend, orders = advertising if advertising is not None else current_ads(store, article, today, scheme)
    config["result"] = calculate(
        config["values"], advertising_spend=spend, orders_count=orders, scenario=scenario
    )
    calculator_result = (
        config["result"]
        if mode == "calculator"
        else calculate(calculator["values"], advertising_spend=spend, orders_count=orders, scenario=scenario)
    )
    config.update(
        {
            "scheme": scheme,
            "advertising_spend": spend,
            "orders_count": orders,
            "advertising_day": today.isoformat(),
            "mode": mode,
            "calculator_values": calculator["values"],
            "calculator_origins": calculator["origins"],
            "calculator_result": calculator_result,
            "calculator_pricing": calculator["pricing"],
            "calculator_advertising": weekly,
        }
    )
    if include_history:
        start, end = (today - timedelta(days=7)).isoformat(), (today - timedelta(days=1)).isoformat()
        rows = repository.history(store, start, end)
        daily = [row["data"] for row in rows if row["article"] == article and row["scheme"] == scheme]
        config["period"] = aggregate(daily, metrics.days_between(start, end))
        config["history"] = daily
    return config


def break_even_scenario(store, article, scheme, *, scenario=None):
    cache = context(store)
    state = detail(store, article, scheme, scenario=scenario, state_cache=cache, mode="calculator")
    prices = break_even_prices(state["values"], scenario=scenario)
    # The button keeps the displayed expense basis, like the WB calculator.
    costs = {
        key: state["values"][key]
        for key in (
            "commission_percent",
            "payment_acceptance",
            "payment_transfer_percent",
            "delivery_cost",
            "tariff_extra",
        )
        if state["values"].get(key) is not None
    }
    updated = {**(scenario or {}), **costs, **prices}
    result = detail(store, article, scheme, scenario=updated, state_cache=cache, mode="calculator")
    result["break_even_scenario"] = updated
    return result


def attach(products, start, end, today, scheme="FBY"):
    histories, contexts, current = {}, {}, {}
    for store in {product["store_slug"] for product in products}:
        histories[store] = repository.history(store, start.isoformat(), end.isoformat())
        contexts[store] = context(store)
        key = today.isoformat()
        ads, ad_days = metrics.get_history(store, "advertising", key, key)
        orders, order_days = metrics.get_history(store, "orders", key, key)
        current[store] = (ads, orders, key in ad_days and key in order_days)
    expected = metrics.days_between(start.isoformat(), end.isoformat())
    for product in products:
        store, article = product["store_slug"], product["article"]
        ads, orders, complete = current[store]
        ad_values = allocate_today(ads, orders, article, scheme) if complete else (None, None)
        config = detail(
            store,
            article,
            scheme,
            today=today,
            state_cache=contexts[store],
            advertising=ad_values,
            include_history=False,
        )
        product["ym_economics"] = config
        product["current_economics"].update(
            {
                "margin": config["result"]["margin"],
                "roi": config["result"]["roi"],
                "orders": config["orders_count"],
                "buyout_percent": config["values"].get("buyout_percent"),
                "advertising_spend": config["advertising_spend"],
                "period_to": today.isoformat(),
            }
        )
        daily = [
            row["data"] for row in histories[store] if row["article"] == article and row["scheme"] == scheme
        ]
        period = aggregate(daily, expected)
        product["economics_7d"].update(
            {
                "margin": period["margin"],
                "roi": period["roi"],
                "purchase_value": period["purchase_value"],
                "margin_coverage": period["coverage"],
                "roi_coverage": period["coverage"],
                "complete": period["complete"],
                "unallocated_advertising": period["unallocated_advertising"],
            }
        )
        config["period"] = period
        config["history"] = daily
    return products


def capture_today(stores, *, today=None, only_article=None):
    """Capture today's input baseline without manufacturing past inputs."""
    from app.repositories.yandex_assortment import active_articles

    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    count = 0
    for store in stores:
        cache = context(store)
        active = active_articles(store)
        for article in active:
            if only_article is not None and article != only_article:
                continue
            for scheme in ("FBY", "FBS"):
                state = current_inputs(store, article, scheme, today=today, state_cache=cache)
                result = calculate(state["values"], without_advertising=True)
                if result["margin"] is not None:
                    repository.save_source(
                        store,
                        article,
                        "day-input:" + today.isoformat() + ":" + scheme,
                        {
                            "values": state["values"],
                            "origins": state["origins"],
                            "result": result,
                            "version": VERSION,
                            "basis": "today_prices",
                            "pricing": state["pricing"],
                        },
                    )
                    count += 1
    return {"captured": count}


def close_days(stores, *, today=None):
    """Finalize only days with saved historical inputs and complete daily metrics.

    New daily records preserve model counts. Legacy mixed-model days are not guessed. SKU advertising is shared between models, allocated by ordered quantity."""
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    completed = 0
    for store in stores:
        sources = repository.sources(store)
        loaded = {}
        closed = {
            (row["article"], row["scheme"], row["day"])
            for row in repository.history(store, "0001-01-01", today.isoformat())
        }
        for (article, source), snapshot in sources.items():
            if not source.startswith("day-input:"):
                continue
            _, day, scheme = source.split(":")
            if day >= today.isoformat() or (article, scheme, day) in closed:
                continue
            if day not in loaded:
                loaded[day] = (
                    metrics.get_history(store, "advertising", day, day),
                    metrics.get_history(store, "orders", day, day),
                )
            (ads, ads_days), (orders, order_days) = loaded[day]
            if day not in ads_days or day not in order_days:
                continue
            state = snapshot["values"]
            if state.get("basis") not in {"today_observations", "today_prices"}:
                continue
            values = state["values"]
            scheme_rows = [row for row in orders if row["article"] == article]

            if any("schemes" not in row for row in scheme_rows):
                continue
            count = sum(
                int(row.get("schemes", {}).get(scheme, {}).get("orders_count", 0)) for row in scheme_rows
            )
            total = sum(int(row.get("orders_count") or 0) for row in scheme_rows)
            spend = sum(float(row.get("spend") or 0) for row in ads if row["article"] == article)

            if total:
                spend = spend * count / total
            elif scheme != "FBY":
                spend = 0
            q = float(values["buyout_percent"]) / 100
            bought = count * q
            unit = state.get("result") or calculate(values, without_advertising=True)
            payload = {
                "day": day,
                "scheme": scheme,
                "inputs": values,
                "origins": state["origins"],
                "orders_count": count,
                "expected_buyouts": bought,
                "advertising_spend": spend,
                "profit": round(unit["margin"] * bought - spend, 2) if unit["margin"] is not None else None,
                "purchase_value": round(values["purchase_price"] * bought, 2),
                "calculation_version": state["version"],
            }
            repository.save_day(store, article, scheme, day, payload)
            completed += 1
    return {"closed": completed}


def bootstrap_1c(stores):
    """Take ownership of existing costs once; later sheet refreshes cannot replace them."""
    count = 0
    for store in stores:
        for article, row in yandex_source_values.get_values(store).items():
            for scheme in ("FBY", "FBS"):
                repository.save_source(
                    store,
                    article,
                    "initial:" + scheme,
                    {key: row.get(key) for key in ("purchase_price", "fulfillment_cost")}
                    | {"other_percent": row.get("team_commission_percent")},
                    initial_only=True,
                )
                count += 1
    return {"initial_rows": count}
