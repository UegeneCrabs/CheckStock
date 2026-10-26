from sqlalchemy import delete

from app.dto.google_export import GoogleExportSettings, GoogleExportUpdate
from app.exports import project_sheet
from app.infrastructure.async_database import table_for, upsert
from app.infrastructure.async_read_models import AsyncReadModels
from app.infrastructure.orm import ProjectSheetExportSettingRecord, ProjectSheetExportTargetRecord


class GoogleExportRepository(AsyncReadModels):
    async def settings(self) -> GoogleExportSettings:
        return await self.read(lambda: GoogleExportSettings.model_validate(project_sheet.get_settings()))

    async def save(self, value: GoogleExportUpdate, updated_at: str) -> None:
        settings = table_for(ProjectSheetExportSettingRecord)
        targets = table_for(ProjectSheetExportTargetRecord)
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                upsert(
                    connection,
                    settings,
                    {
                        "id": 1,
                        "enabled": int(value.enabled),
                        "schedule_kind": value.schedule_kind,
                        "weekday": value.weekday,
                        "run_time": value.run_time,
                        "updated_at": updated_at,
                    },
                    ("id",),
                )
            )
            await connection.execute(delete(targets))
            await connection.execute(targets.insert(), [target.model_dump() for target in value.targets])
