"""Accounts that participate in the shared marketplace exports."""

from app.core.stores import STORES


def stores_for_marketplace(marketplace: str) -> tuple[str, ...]:
    # TOYKA has no Yandex account. Do not infer participation from credentials:
    # a missing key for an existing account must still fail before publication.
    return tuple(store for store in STORES if marketplace != "YANDEX MARKET" or store != "toyka")
