"""Quote a selected leaf category for a calculator scenario, without changing the offer."""

from app.yandex import categories, economics, economics_api


def select_category(store, article, scheme, scenario):
    tree = categories.catalog(store)
    by_id = {item["id"]: item for item in tree["items"]}
    selected = by_id.get(scenario.get("category_id"))
    if selected is None:
        raise ValueError("Выберите категорию из справочника ЯМ.")
    if not selected["leaf"]:
        raise ValueError("У категории есть подкатегории. Выберите конечную подкатегорию.")
    path, node = [], selected
    while node is not None:
        path.insert(0, {"id": node["id"], "name": node["name"]})
        node = by_id.get(node["parent_id"])
    scenario = {**scenario, "category_name": selected["name"]}
    scenario.pop("commission_percent", None)
    quoted = economics_api.quote(store, article, scheme, scenario=scenario, persist=False)
    if quoted["components"].get("commission_percent") is None:
        raise ValueError("ЯМ не вернул комиссию выбранной категории.")
    updated = {
        **scenario,
        **{
            key: value
            for key, value in quoted["components"].items()
            if key == "commission_percent" or scenario.get(key) is None
        },
    }
    result = economics.detail(store, article, scheme, scenario=updated, mode="calculator")
    for key in quoted["components"]:
        if key == "commission_percent" or scenario.get(key) is None:
            result["origins"][key] = "API: тариф выбранной категории"
    result["category"] = {
        "category_id": selected["id"],
        "category_name": selected["name"],
        "path": path,
        "leaf": True,
    }
    result["category_scenario"] = updated
    result["tariff"] = {"valid": True, "approximate": True, "services": quoted["services"]}
    return result
