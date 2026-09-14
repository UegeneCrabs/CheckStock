"""Scheduled and manual economics updates; never writes to a marketplace or sheet."""

from app.yandex import economics, economics_api, tokens

JOB = "yandex_economics_sync"


def sync_all(store_slugs=None):
    stores = tuple(store_slugs) if store_slugs is not None else tuple(tokens.stores_with_credentials())
    result = {}
    economics.bootstrap_1c(stores)
    for store in stores:
        try:
            result[store] = {"ok": True, **economics_api.refresh_store(store)}
        except Exception as error:
            result[store] = {"ok": False, "error": type(error).__name__}
        result[store].update(economics.capture_today((store,)))
        result[store].update(economics.close_days((store,)))
    return result
