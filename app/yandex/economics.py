"""Persistent YM economics. Reads never call marketplace APIs or write snapshots."""

from datetime import datetime, timedelta

from app.core.domain import MOSCOW_TIMEZONE
from app.economics.wb.calculations import resolve_buyout_percent
from app.repositories import unit_economics_yandex as metrics
from app.repositories import yandex_economics as repository
from app.repositories import yandex_source_values, yandex_storefront
from app.yandex import economics_shared as shared
from app.yandex.category_commissions import SOURCE as CATEGORY_COMMISSION_SOURCE
from app.yandex.category_commissions import commission_value
from app.yandex.economics_advertising import apply_calculator_drr, product_drr, weekly_history
from app.yandex.economics_calculation import (
    CABINET_DEFAULTS,
    REMOVED_FIELDS,
    VERSION,
    aggregate,
    break_even_prices,
    calculate,
    delivery_components,
    resolve,
    sheet_logistics,
)
from app.yandex.economics_days import resolve_period
from app.yandex.economics_diagnostics import current_issues
from app.yandex.price_calculation import discounted_price


def context(store, *, sources=None):
    read = sources.read if sources is not None else lambda loader, *args: loader(*args)
    return {
        "sources": read(repository.sources, store),
        "settings": read(repository.all_settings, store),
        "source_1c": read(yandex_source_values.get_values, store),
        "prices": read(yandex_storefront.get_prices, store),
        "snapshots": read(metrics.get_snapshots, store),
        "buyout_settings": read(metrics.get_buyout_settings, store),
    }


def effective(store, article, scheme, *, scenario=None, state_cache=None, estimate_tariff=False):
    """Current quotes must match; planning can keep the last compatible saved tariff."""
    cache = state_cache if state_cache is not None else context(store)
    saved_sources = cache["sources"]
    seed = saved_sources.get((article, "initial:FBY"), {}) or saved_sources.get((article, "initial:FBS"), {})
    catalog = saved_sources.get((article, "catalog"), {})
    category = saved_sources.get((article, "category"), {})
    category_values = category.get("values", {})
    cabinet = shared.settings(cache["settings"], "")
    product = shared.settings(cache["settings"], article)
    source_1c = cache["source_1c"].get(article, {})
    base = {
        "purchase_price": source_1c.get("purchase_price"),
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
    buyout = resolve_buyout_percent(
        buyouts.get(article, {}).get("buyout_percent"),
        buyout_settings.get("default_buyout_percent"),
    )
    layers = [
        ("Начальные данные 1С", base),
        ("Перенесено из листа", seed.get("values", {})),
        ("API: каталог", catalog.get("values", {})),
        *price_layers,
        ("API: выкуп", {"buyout_percent": buyout}),
        ("По умолчанию", CABINET_DEFAULTS),
        ("Настройки кабинета", cabinet["values"]),
        ("Изменено на сайте", product["values"]),
    ]
    if category.get("updated_at", "") >= catalog.get("updated_at", ""):
        layers.insert(
            3, ("API: категория", {key: category_values.get(key) for key in ("category_id", "category_name")})
        )
    values, _ = resolve(*layers, ("Сценарий", scenario or {}))
    tariff, tariff_valid, estimated_tariff = common_tariff(
        saved_sources, article, values, estimate=estimate_tariff
    )
    tariff_data = tariff.get("values", {})
    source_scheme = tariff_data.get("signature", {}).get("scheme", "FBY")
    commission = saved_sources.get((article, CATEGORY_COMMISSION_SOURCE + source_scheme), {}).get(
        "values", {}
    )
    reference_percent = commission_value(commission, values)
    if reference_percent is not None:
        layers.insert(-2, ("API: комиссия категории за неделю", {"commission_percent": reference_percent}))
    tariff_fresh = yandex_storefront.fresh(tariff.get("updated_at"))
    if tariff_valid or estimated_tariff:
        origin = "Последний загруженный тариф API" if estimated_tariff else "API: тариф"
        # Legacy API quotes must not restore the removed withdrawal fee
        # or overwrite the configurable payment-transfer rate.
        components = {
            key: value
            for key, value in tariff_data.get("components", {}).items()
            if key not in {"payment_transfer_percent", "acquiring_percent"}
        }
        components = {**delivery_components(tariff_data.get("services", [])), **components}
        # A saved quote is a planning fallback, not a replacement for a current category rate.
        if estimated_tariff and reference_percent is not None:
            components = {key: value for key, value in components.items() if key != "commission_percent"}
        layers.insert(-2, (origin, components))
    values, origins = resolve(*layers, ("Сценарий", scenario or {}))
    # Use the latest 1C import, never an obsolete seed or saved legacy override.
    fulfillment_override = (scenario or {}).get("fulfillment_cost")
    values["fulfillment_cost"] = (
        source_1c.get("fulfillment_cost") if fulfillment_override is None else fulfillment_override
    )
    origins["fulfillment_cost"] = (
        "Google-таблица 1С · лист YM · колонка «Проч. затр., руб»"
        if fulfillment_override is None
        else "Сценарий"
    )
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
        "overrides": {
            key: value
            for key, value in product["values"].items()
            if key not in REMOVED_FIELDS and key != "fulfillment_cost"
        },
        "cabinet_revision": cabinet["revision"],
        "pricing": pricing,
        "category": category_values
        if category_values.get("category_id") == values.get("category_id")
        else {},
        "category_commission": {**commission, "valid": reference_percent is not None},
        "tariff": {
            "source_scheme": source_scheme,
            "valid": tariff_valid,
            "approximate": bool(estimated_tariff),
            "stale": bool(estimated_tariff and not tariff_fresh),
            "updated_at": tariff.get("updated_at"),
            "services": tariff_data.get("services", []),
        },
    }


def apply_sheet_logistics(values, origins, *, scenario=None):
    from app.yandex.economics_calculation import DELIVERY_FIELDS

    if (
        any((scenario or {}).get(key) is not None for key in DELIVERY_FIELDS)
        and (scenario or {}).get("delivery_cost") is None
    ):
        parts = [values.get(key) for key in DELIVERY_FIELDS]
        values["delivery_cost"] = sum(parts) if all(part is not None for part in parts) else None
        origins["delivery_cost"] = "Сумма составляющих доставки в сценарии"
    derived = sheet_logistics(values, scenario=scenario)
    values.update(derived)
    origins.update(
        volume_l="Габариты упаковки: длина × ширина × высота / 1000",
        return_middle_mile="Средняя миля из тарифа доставки ЯМ",
        return_cost="Средняя миля из тарифа доставки ЯМ + 15 ₽",
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
    )
    # Invalidate legacy quotes that silently replaced missing acquiring with zero.
    return {**{key: values.get(key) for key in keys}, "scheme": scheme, "quote_source": "campaign-v2"}


def checked_today(timestamp, today):
    try:
        return datetime.fromisoformat(timestamp).astimezone(
            MOSCOW_TIMEZONE
        ).date() == today and yandex_storefront.fresh(timestamp)
    except (TypeError, ValueError):
        return False


def common_tariff(sources, article, values, *, estimate=False, today=None):
    """Reuse one compatible quote for both views, retaining its actual API model."""
    candidates = [
        sources.get((article, "tariff:" + scheme), {}) for scheme in (shared.SCHEME, *shared.SOURCE_SCHEMES)
    ]
    for approximate in (False, True) if estimate else (False,):
        for record in candidates:
            signature = record.get("values", {}).get("signature")
            if not isinstance(signature, dict) or signature.get("scheme") not in shared.SOURCE_SCHEMES:
                continue
            expected = tariff_signature(values, signature["scheme"])
            if approximate:
                matches = {k: v for k, v in signature.items() if k != "seller_price"} == {
                    k: v for k, v in expected.items() if k != "seller_price"
                }
            else:
                matches = signature == expected and (
                    checked_today(record.get("updated_at"), today)
                    if today
                    else yandex_storefront.fresh(record.get("updated_at"))
                )
            if matches:
                return record, not approximate, approximate
    return {}, False, False


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
            "advertising_per_buyout",
            "logistics_total",
            "logistics_returns",
            "repeat_delivery",
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

    tariff, tariff_valid, _ = common_tariff(cache["sources"], article, values, today=today)
    quote = tariff.get("values", {})
    state["tariff"]["valid"] = tariff_valid
    reference_percent = commission_value(state["category_commission"], values)
    state["category_commission"]["valid"] = reference_percent is not None
    fields = (
        "commission_percent",
        "payment_acceptance",
        "delivery_cost",
        "delivery_customer",
        "middle_mile",
        "delivery_other",
    )
    components = {**delivery_components(quote.get("services", [])), **quote.get("components", {})}
    for field in fields:
        if origins.get(field) in {"Изменено на сайте", "Настройки кабинета", "Сценарий"}:
            continue
        if field == "commission_percent" and not tariff_valid and reference_percent is not None:
            values[field] = reference_percent
            origins[field] = "API: комиссия категории за неделю"
            continue
        values[field] = components.get(field) if tariff_valid else None
        origins[field] = "API: тариф за сегодня" if tariff_valid else "Нет тарифа за сегодня"
    apply_sheet_logistics(values, origins, scenario=scenario)
    return state


def allocate_today(ads, orders, article, scheme):
    rows = [row for row in orders if row["article"] == article]
    spend = sum(float(row.get("spend") or 0) for row in ads if row["article"] == article)
    total = sum(int(row.get("orders_count") or 0) for row in rows)
    return spend, total


def current_metrics(store, article, today, scheme="FBY", *, history=None):
    key = today.isoformat()
    if history is None:
        ads, ads_days = metrics.get_history(store, "advertising", key, key)
        orders, order_days = metrics.get_history(store, "orders", key, key)
    else:
        ads, ads_days, orders, order_days = history
    issues = []
    if key not in ads_days:
        issues.append("Не загружены расходы на рекламу за сегодня (" + key + ", МСК).")
    orders_known = key in order_days
    if not orders_known:
        issues.append("Не загружены заказы за сегодня (" + key + ", МСК).")
    spend, count = allocate_today(ads, orders, article, scheme)
    if not orders_known:
        count = None
    if key not in ads_days:
        spend = None
    return {"spend": spend, "orders": count, "issues": issues}


def detail(
    store,
    article,
    scheme="FBY",
    *,
    today=None,
    scenario=None,
    state_cache=None,
    daily_metrics=None,
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
    daily = daily_metrics if daily_metrics is not None else current_metrics(store, article, today, scheme)
    if mode == "current":
        from app.repositories import daily_economics

        ledger = daily_economics.get(("YANDEX MARKET", store, article, today.isoformat()))
        if ledger:
            config["values"] = dict(ledger["values"])
            daily = dict(daily)
            for field, name in (("orders_count", "orders"), ("advertising_spend", "spend")):
                if field in ledger["overrides"]:
                    daily[name] = ledger["overrides"][field]
            daily["issues"] = [message for message in daily["issues"]
                               if not ("расходы на рекламу" in message and daily["spend"] is not None)
                               and not ("заказы за сегодня" in message and daily["orders"] is not None)]
    spend, orders = daily["spend"], daily["orders"]
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
            "shared_schemes": True,
            "advertising_spend": spend,
            "orders_count": orders,
            "advertising_day": today.isoformat(),
            "mode": mode,
            "calculator_values": calculator["values"],
            "calculator_origins": calculator["origins"],
            "calculator_result": calculator_result,
            "calculator_pricing": calculator["pricing"],
            "calculator_tariff": calculator["tariff"],
            "calculator_advertising": weekly,
        }
    )
    if mode == "current":
        from app.economics.daily_calculation import calculate as calculate_daily

        config["day_result"] = calculate_daily("YANDEX MARKET", {**config["values"], "orders_count": orders, "advertising_spend": spend})[1]
        config["current_issues"] = current_issues(config, daily)
    if include_history:
        start, end = (today - timedelta(days=7)).isoformat(), (today - timedelta(days=1)).isoformat()
        rows = repository.history(store, start, end)
        daily = resolved_history(store, article, rows, metrics.days_between(start, end))
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
            "acquiring_percent",
            "delivery_cost",
            "delivery_customer",
            "middle_mile",
            "delivery_other",
        )
        if state["values"].get(key) is not None
    }
    updated = {**(scenario or {}), **costs, **prices}
    result = detail(store, article, scheme, scenario=updated, state_cache=cache, mode="calculator")
    result["break_even_scenario"] = updated
    return result


def attach(products, start, end, today, scheme="FBY"):
    histories, contexts, current, period_sources = {}, {}, {}, {}
    for store in {product["store_slug"] for product in products}:
        histories[store] = repository.history(store, start.isoformat(), end.isoformat())
        contexts[store] = context(store)
        key = today.isoformat()
        ads, ad_days = metrics.get_history(store, "advertising", key, key)
        orders, order_days = metrics.get_history(store, "orders", key, key)
        current[store] = (ads, ad_days, orders, order_days)
        period_sources[store] = (
            metrics.get_history(store, "orders", start.isoformat(), end.isoformat()),
            metrics.get_history(store, "advertising", start.isoformat(), end.isoformat()),
        )
    expected = metrics.days_between(start.isoformat(), end.isoformat())
    for product in products:
        store, article = product["store_slug"], product["article"]
        daily = current_metrics(store, article, today, scheme, history=current[store])
        config = detail(
            store,
            article,
            scheme,
            today=today,
            state_cache=contexts[store],
            daily_metrics=daily,
            include_history=False,
        )
        product["ym_economics"] = config
        required_inputs = calculate(config["values"], without_advertising=True)
        product["data_errors"] = list(dict.fromkeys(
            (product.get("data_errors") or [])
            + config["current_issues"]["margin"]
            + [
                issue for issue in config["current_issues"]["roi"]
                if not issue.startswith("Сегодня нет выкупленных товаров")
            ]
            + required_inputs["messages"]
        ))
        product["current_economics"].update(
            {
                "margin": config["result"]["margin"],
                "day_profit": config["day_result"]["day_profit"], "expected_buyouts": config["day_result"]["expected_buyouts"],
                "daily_complete": config["day_result"]["daily_complete"],
                "daily_messages": config["day_result"]["daily_messages"],
                "day_purchase_value": config["day_result"]["purchase_value"],
                "purchase_value": config["values"].get("purchase_price"),
                "roi": config["result"]["roi"],
                "orders": config["orders_count"],
                "buyout_percent": config["values"].get("buyout_percent"),
                "advertising_spend": config["advertising_spend"],
                "period_to": today.isoformat(),
                "issues": config["current_issues"],
                "complete": config["result"]["complete"], "status": config["result"]["status"],
                "messages": config["result"]["messages"], "missing": config["result"]["missing"],
            }
        )
        daily = resolved_history(store, article, histories[store], expected, sources=period_sources[store])
        period = aggregate(daily, expected)
        product["economics_7d"].update(
            {
                "margin": period["margin"],
                "roi": period["roi"],
                "purchase_value": period["purchase_value"],
                "roi_purchase_value": period["roi_purchase_value"],
                "margin_coverage": period["coverage"],
                "roi_coverage": period["coverage"],
                "complete": period["complete"],
                "messages": period["messages"],
                "unallocated_advertising": period["unallocated_advertising"],
            }
        )
        config["period"] = period
        config["history"] = daily
    return products


def resolved_history(store, article, rows, days, *, sources=None):
    (orders, order_days), (ads, ad_days) = sources or (
        metrics.get_history(store, "orders", days[0], days[-1]),
        metrics.get_history(store, "advertising", days[0], days[-1]),
    )
    saved = {r["day"]: r for r in shared.history(rows, article)}
    return list(resolve_period(days, saved,
        {r["day"]: r for r in orders if r["article"] == article},
        {r["day"]: r for r in ads if r["article"] == article}, order_days, ad_days).values())


def capture_today(stores, *, today=None, only_article=None):
    """Save all observed inputs, including incomplete products, for today only."""
    from app.repositories import daily_economics
    from app.repositories.yandex_assortment import active_articles

    actual_today = datetime.now(MOSCOW_TIMEZONE).date()
    today = today or actual_today
    if today != actual_today:
        raise ValueError("Нельзя сохранять сегодняшние настройки задним числом.")
    count = 0
    for store in stores:
        cache = context(store)
        day = today.isoformat()
        ads, ad_days = metrics.get_history(store, "advertising", day, day)
        orders, order_days = metrics.get_history(store, "orders", day, day)
        for article in active_articles(store):
            if only_article is not None and article != only_article:
                continue
            state = current_inputs(store, article, shared.SCHEME, today=today, state_cache=cache)
            daily = current_metrics(store, article, today, history=(ads, ad_days, orders, order_days))
            values = {**state["values"], "orders_count": daily["orders"], "advertising_spend": daily["spend"]}
            source = daily_economics.observation(
                values, version=VERSION,
                origins={**state["origins"], "orders_count": "ЯМ: заказы за день", "advertising_spend": "ЯМ: реклама за день"},
                raw={
                    "sources": {name: record for (sku, name), record in cache["sources"].items() if sku == article and not name.startswith("day-input:")},
                    "settings": {str(key): value for key, value in cache["settings"].items() if key[0] in {"", article}},
                    "source_1c": cache["source_1c"].get(article), "prices": cache["prices"].get(article),
                    "orders": [r for r in orders if r["article"] == article],
                    "advertising": [r for r in ads if r["article"] == article],
                },
            )
            daily_economics.capture(("YANDEX MARKET", store, article, day), source)
            # Compatibility baseline, independent of whether margin is calculable.
            repository.save_source(store, article, "day-input:" + day + ":" + shared.SCHEME, {
                "values": values, "origins": source["origins"],
                "result": calculate(values, without_advertising=True), "version": VERSION,
                "basis": "today_prices", "pricing": state["pricing"],
            })
            count += 1
    return {"captured": count}


def close_days(stores, *, today=None):
    """Refresh daily orders/ads on saved inputs; never borrow today's costs.

    Incomplete days remain visible and can become complete after a source refresh
    or a dated manual correction. Overlays survive both operations.
    """
    from app.repositories import daily_economics

    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    completed = 0
    for store in stores:
        candidates = {(r["article"], r["day"]): r for r in daily_economics.records(
            "YANDEX MARKET", (store,), "0001-01-01", (today - timedelta(days=1)).isoformat()
        )}
        for (article, name), snapshot in repository.sources(store).items():
            if not name.startswith("day-input:"):
                continue
            _, day, scheme = name.split(":")
            if day >= today.isoformat() or scheme != shared.SCHEME or (article, day) in candidates:
                continue
            if snapshot["values"].get("basis") not in {"today_prices", "today_observations"}:
                continue
            saved = daily_economics.get(("YANDEX MARKET", store, article, day))
            if saved:
                candidates[article, day] = saved
        loaded = {}
        for (article, day), saved in candidates.items():
            if day not in loaded:
                loaded[day] = (metrics.get_history(store, "advertising", day, day), metrics.get_history(store, "orders", day, day))
            (ads, ad_days), (orders, order_days) = loaded[day]
            source = saved["source"]
            v = dict(source["values"])
            count = sum(int(r.get("orders_count") or 0) for r in orders if r["article"] == article) if day in order_days else v.get("orders_count")
            spend = sum(float(r.get("spend") or 0) for r in ads if r["article"] == article) if day in ad_days else v.get("advertising_spend")
            if count == v.get("orders_count") and spend == v.get("advertising_spend") and saved["revision"]:
                continue
            daily_economics.refresh_metrics(
                ("YANDEX MARKET", store, article, day),
                {"orders_count": count, "advertising_spend": spend},
                {"received_at": daily_economics.now(), "orders": [r for r in orders if r["article"] == article], "advertising": [r for r in ads if r["article"] == article]},
            )
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
                    {"purchase_price": row.get("purchase_price")},
                    initial_only=True,
                )
                count += 1
    return {"initial_rows": count}
