from __future__ import annotations

from datetime import UTC, datetime

from app import db
from app.stores import STORES
from app.sync_catalog import SyncJobDefinition, job_definitions

MARKETPLACE_LABELS = {
    "WB": "Wildberries",
    "OZON": "Ozon",
    "YANDEX MARKET": "Яндекс Маркет",
}


def definitions_by_name() -> dict[str, SyncJobDefinition]:
    return {definition.name: definition for definition in job_definitions()}


def definition_for(name: str) -> SyncJobDefinition | None:
    return definitions_by_name().get(name)


def _setting_map(name: str) -> dict[tuple[str, str], bool]:
    rows = db.list_sync_job_settings(name)
    return {
        (str(row.get("store_slug") or ""), str(row.get("marketplace") or "")): bool(
            row.get("enabled")
        )
        for row in rows
    }


def _targets(definition: SyncJobDefinition) -> tuple[tuple[str, str], ...]:
    if definition.scope == "stores":
        return tuple((store_slug, "") for store_slug in STORES)
    if definition.scope == "store_marketplaces":
        return tuple(
            (store_slug, marketplace)
            for store_slug in STORES
            for marketplace in definition.marketplaces
        )
    return ()


def configuration(name: str) -> dict:
    definition = definition_for(name)
    if definition is None:
        raise ValueError("Выгрузка не найдена")
    settings = _setting_map(name)
    configured_enabled = settings.get(("", ""), True)
    marketplace_settings = [
        {
            "marketplace": marketplace,
            "marketplace_name": MARKETPLACE_LABELS.get(marketplace, marketplace),
            "enabled": settings.get(("", marketplace), True),
        }
        for marketplace in definition.marketplaces
    ]
    marketplaces_enabled = {
        item["marketplace"]: item["enabled"] for item in marketplace_settings
    }
    targets = [
        {
            "store_slug": store_slug,
            "store_name": STORES[store_slug].name,
            "marketplace": marketplace,
            "marketplace_name": MARKETPLACE_LABELS.get(marketplace, marketplace),
            "configured_enabled": settings.get((store_slug, marketplace), True),
            "enabled": settings.get((store_slug, marketplace), True)
            and marketplaces_enabled.get(marketplace, True),
        }
        for store_slug, marketplace in _targets(definition)
    ]
    enabled_targets = sum(1 for target in targets if target["enabled"])
    effective_enabled = bool(definition.enabled and configured_enabled)
    if not definition.enabled:
        summary = "Системно отключена"
    elif not configured_enabled:
        summary = "Выключена"
    elif targets and enabled_targets < len(targets):
        summary = f"Частично: {enabled_targets} из {len(targets)}"
    else:
        summary = "Включена"
    return {
        "name": name,
        "scope": definition.scope,
        "environment_enabled": bool(definition.enabled),
        "configured_enabled": configured_enabled,
        "effective_enabled": effective_enabled,
        "summary": summary,
        "target_count": len(targets),
        "enabled_target_count": enabled_targets,
        "marketplace_settings": marketplace_settings,
        "targets": targets,
    }


def enabled_stores(name: str, marketplace: str = "") -> tuple[str, ...]:
    config = configuration(name)
    if not config["effective_enabled"]:
        return ()
    if config["scope"] == "global":
        return tuple(STORES)
    return tuple(
        target["store_slug"]
        for target in config["targets"]
        if target["enabled"] and target["marketplace"] == marketplace
    )


def has_enabled_targets(name: str) -> bool:
    config = configuration(name)
    if not config["effective_enabled"]:
        return False
    return not config["targets"] or bool(config["enabled_target_count"])


def save_setting(
    name: str,
    *,
    enabled: bool,
    store_slug: str = "",
    marketplace: str = "",
) -> dict:
    definition = definition_for(name)
    if definition is None:
        raise ValueError("Выгрузка не найдена")
    if not store_slug and not marketplace:
        pass
    elif (
        definition.scope == "store_marketplaces"
        and not store_slug
        and marketplace in definition.marketplaces
    ):
        pass
    elif definition.scope == "stores":
        if store_slug not in STORES or marketplace:
            raise ValueError("Недопустимая настройка магазина")
    elif definition.scope == "store_marketplaces":
        if store_slug not in STORES or marketplace not in definition.marketplaces:
            raise ValueError("Недопустимая настройка магазина или маркетплейса")
    else:
        raise ValueError("Для этой выгрузки нет настроек по магазинам")
    db.set_sync_job_setting(
        name,
        store_slug,
        marketplace,
        enabled,
        datetime.now(UTC).isoformat(),
    )
    return configuration(name)
