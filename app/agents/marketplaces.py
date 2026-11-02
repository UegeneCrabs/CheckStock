from app.core.constants import AGENT_OZON


def supports(name: str, marketplace: str) -> bool:
    from app.agents.yandex_reports import SUPPORTED
    from app.web.routers.agent_full import SPECS

    if marketplace == AGENT_OZON.marketplace:
        return name in AGENT_OZON.supported
    if name in AGENT_OZON.exclusive:
        return False
    common = name in AGENT_OZON.discovery or (name in SPECS and not SPECS[name][1])
    if marketplace == "YANDEX MARKET":
        return common or name in SUPPORTED or name == "loss-products"
    return marketplace == "WB"
