from app.application.ozon_limits import account_digest
from app.config import settings
from app.core.stores import STORES
from app.dto.ozon_limits import PerformanceAccount, PerformanceIdentity
from app.ozon import tokens


def performance_identity(store: str, client_id: str) -> PerformanceIdentity:
    account_ids = {
        slug: account_digest(tokens.get_performance_credentials(slug)[0])
        for slug in STORES
        if tokens.has_performance_credentials(slug)
    }
    account_ids[store] = account_digest(client_id)
    organizations: dict[str, set[str]] = {}
    configured = settings.ozon_api_limits.performance_organizations
    for slug, account in account_ids.items():
        if slug in configured:
            organizations.setdefault(account, set()).add(configured[slug].strip())
    if any(len(values) > 1 for values in organizations.values()):
        raise ValueError("One Performance account cannot belong to multiple organizations")
    defaults = {
        account: min(slug for slug, value in account_ids.items() if value == account)
        for account in account_ids.values()
    }
    accounts = tuple(
        PerformanceAccount(
            store_slug=slug,
            account_scope=account,
            organization_scope=account_digest(next(iter(organizations[account])))
            if account in organizations
            else account_digest(defaults[account]),
        )
        for slug, account in sorted(account_ids.items())
    )
    primary = next(item for item in accounts if item.store_slug == store)
    return PerformanceIdentity(**primary.model_dump(), accounts=accounts)
