"""Scheduled and manual economics updates; never writes to a marketplace or sheet."""

from app.yandex import economics, economics_api, tokens

JOB = "yandex_economics_sync"


def sync_all(store_slugs=None):
    stores = tuple(
        store
        for store in (store_slugs if store_slugs is not None else tokens.stores_with_credentials())
        if tokens.has_credentials(store)
    )
    result = {}
    economics.bootstrap_1c(stores)
    for store in stores:
        try:
            report = economics_api.refresh_store(store)
            missing = report.get("missing") or {}
            result[store] = {"ok": not missing, **report}
            if missing:
                examples = "; ".join(f"{article}: {error}" for article, error in list(missing.items())[:5])
                result[store]["error"] = f"Тарифы не получены для {len(missing)} позиций. {examples}"
        except Exception as error:
            result[store] = {"ok": False, "error": f"{type(error).__name__}: {str(error)[:700]}"}
        result[store].update(economics.capture_today((store,)))
        result[store].update(economics.close_days((store,)))
    return result
