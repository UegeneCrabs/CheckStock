from app.yandex import sync as ya_sync

MARKETPLACE_SCHEMES = {
    "WB": [("fbs", "Текущий сток в продаже FBS"), ("fbo", "Текущий сток в продаже FBO")],
    "OZON": [
        ("fbs", "Текущий сток в продаже FBS"),
        ("rfbs", "Текущий сток в продаже rFBS"),
        ("fbo", "Текущий сток в продаже FBO"),
    ],
    "YANDEX MARKET": [
        ("fbo", "FBY — склады Маркета"),
        ("fbs", "FBS — склады продавца"),
    ],
}


def schemes_for(marketplace: str, store_slug: str = "") -> list[tuple[str, str]]:

    if marketplace == "YANDEX MARKET" and store_slug:
        schemes = ya_sync.store_schemes(store_slug)
        if schemes:
            return schemes
    return MARKETPLACE_SCHEMES.get(marketplace, MARKETPLACE_SCHEMES["WB"])
