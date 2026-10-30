from datetime import UTC, datetime

from sqlalchemy import select

from app.core import health as health_service
from app.core.constants import OZON_ADVERTISING, OZON_API, Marketplace, UiAlertCode, UiAlertScope
from app.dto.integrations import JobRun, JobState, SyncSettingUpdate
from app.dto.ui import UiAlert
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.async_job_repository import RUNS, SETTINGS, STATES
from app.infrastructure.orm import (
    ActivityLogRecord,
    SyncHealthRecord,
    UnitEconomics1CAdvertisingSyncStateRecord,
    WbTokenInfoRecord,
)
from app.wb.token_watch import WARN_DAYS


class IntegrationRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def alerts(self, store_slugs: tuple[str, ...]) -> tuple[UiAlert, ...]:
        health = table_for(SyncHealthRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(
                    health.c.store_slug,
                    health.c.marketplace,
                    health.c.scope,
                    health.c.error,
                    health.c.checked_at,
                ).where(
                    health.c.store_slug.in_(store_slugs),
                    health.c.ok == 0,
                    ~(
                        (health.c.marketplace == Marketplace.OZON)
                        & (health.c.scope == OZON_ADVERTISING.legacy_health_scope)
                    ),
                )
            )
            alerts = []
            for row in rows.mappings():
                if (
                    row["marketplace"] == Marketplace.OZON
                    and row["scope"] == UiAlertScope.REPUTATION
                    and OZON_API.subscription_error_marker in str(row["error"] or "").casefold()
                ):
                    continue
                updated = health_service._credentials_updated_at(row["marketplace"])
                checked = health_service._checked_at(dict(row))
                if updated and checked and checked < updated:
                    continue
                alerts.append(
                    UiAlert.model_validate(
                        dict(row)
                        | {
                            "code": UiAlertCode.ACCESS_DENIED
                            if health_service.requires_team_action(row["error"])
                            else UiAlertCode.SYNC_ERROR,
                        }
                    )
                )
            tokens = table_for(WbTokenInfoRecord)
            result = await connection.execute(select(tokens).where(tokens.c.store_slug.in_(store_slugs)))
            now = datetime.now(UTC)
            for row in result.mappings():
                if not row["expires_at"]:
                    continue
                try:
                    expires = datetime.fromisoformat(row["expires_at"])
                except ValueError:
                    continue
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=UTC)
                if (expires - now).days <= WARN_DAYS:
                    alerts.append(
                        UiAlert(
                            store_slug=row["store_slug"],
                            marketplace="WB",
                            scope=UiAlertScope.CREDENTIALS,
                            code=UiAlertCode.TOKEN_EXPIRING,
                            expires_at=row["expires_at"],
                        )
                    )
            advertising = table_for(UnitEconomics1CAdvertisingSyncStateRecord)
            result = await connection.execute(
                select(advertising).where(
                    advertising.c.store_slug.in_(store_slugs), advertising.c.status == "error"
                )
            )
            for row in result.mappings():
                if health_service.requires_team_action(row["error"]):
                    alerts.append(
                        UiAlert(
                            store_slug=row["store_slug"],
                            marketplace="WB",
                            scope=UiAlertScope.ADVERTISING,
                            code=UiAlertCode.ACCESS_DENIED,
                            error=row["error"],
                        )
                    )
            return tuple(alerts)

    async def state(self, name: str) -> JobState:
        async with self.database.transaction() as connection:
            row = (await connection.execute(select(STATES).where(STATES.c.name == name))).mappings().first()
            return JobState.model_validate(row) if row else JobState(name=name)

    async def runs(self, name: str, limit: int) -> tuple[JobRun, ...]:
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(RUNS).where(RUNS.c.name == name).order_by(RUNS.c.started_at.desc()).limit(limit)
            )
            return tuple(JobRun.model_validate(row) for row in rows.mappings())

    async def save_setting(self, name: str, value: SyncSettingUpdate) -> None:
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                upsert(
                    connection,
                    SETTINGS,
                    {
                        "name": name,
                        "store_slug": value.store_slug,
                        "marketplace": value.marketplace,
                        "enabled": int(value.enabled),
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                    ("name", "store_slug", "marketplace"),
                )
            )

    async def audit(self, user_id: int, user_name: str, action: str, details: str) -> None:
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                table_for(ActivityLogRecord)
                .insert()
                .values(
                    user_id=user_id,
                    user_name=user_name,
                    action=action,
                    details=details,
                    created_at=datetime.now(UTC).isoformat(),
                )
            )
