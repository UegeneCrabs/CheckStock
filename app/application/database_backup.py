from datetime import date

from app.application.ports import BackupDestination, BackupStore
from app.dto.database_backup import BackupJobResult


class DatabaseBackupService:
    def __init__(self, store: BackupStore, destination: BackupDestination):
        self.store, self.destination = store, destination

    async def run(self, day: date) -> BackupJobResult:
        if receipt := await self.store.receipt(day):
            await self.store.cleanup(receipt.artifact)
            return BackupJobResult(file_id=receipt.file_id, skipped=True)
        await self.destination.check_folder()
        upload = await self.store.prepare(day)
        remote = await self.destination.upload(upload, self.store.save_upload)
        await self.store.complete(upload.artifact, remote.id)
        await self.store.cleanup(upload.artifact)
        return BackupJobResult(file_id=remote.id)
