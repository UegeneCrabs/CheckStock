"""Human-readable access status for individual Yandex Market shops."""

ACCESS_REASONS = {
    "MANUALLY_DISABLED": "API выключено вручную в кабинете Яндекс Маркета",
    "DISABLED_BY_INACTIVITY": "API отключено из-за неактивности магазина",
    "DISABLED_BY_NO_ACTIVE_CONTRACT": "нет действующего договора с Маркетом",
    "DISABLED_BY_NO_PLACEMENT_TYPE": "магазин не подключён к программе размещения",
}


def campaign_access_issue(campaign: dict) -> str:
    status = campaign.get("apiAvailability") or "UNKNOWN"
    name = campaign.get("domain") or "Магазин"
    reason = ACCESS_REASONS.get(status, "статус доступа к API не подтверждён")
    return f"{name} (ID {campaign['id']}): {reason} ({status})"


def connection_access_status(campaigns: list[dict], business_id: int) -> dict:
    own = [campaign for campaign in campaigns
           if int((campaign.get("business") or {}).get("id") or campaign.get("businessId") or 0)
           == int(business_id)]
    if not own:
        return {"status": "error", "detail": "Нет доступа к указанному Business ID"}
    unavailable = [campaign for campaign in own if campaign.get("apiAvailability") != "AVAILABLE"]
    if not unavailable:
        return {"status": "connected", "detail": f"API доступно для всех магазинов ({len(own)})"}
    return {
        "status": "partial" if len(unavailable) < len(own) else "unavailable",
        "detail": f"API доступно для {len(own) - len(unavailable)} из {len(own)} магазинов. "
                  + "; ".join(campaign_access_issue(campaign) for campaign in unavailable),
    }
