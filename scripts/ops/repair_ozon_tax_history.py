import argparse
import asyncio
import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.container import ozon_tax_history_service
from app.dto.ozon_tax_history import OzonTaxHistoryCommand
from app.infrastructure.async_database import AsyncDatabase


async def run(command: OzonTaxHistoryCommand, backup_directory: Path | None) -> None:
    database = AsyncDatabase()
    try:
        service = ozon_tax_history_service(database)
        plan = await service.plan(command)
        backup = None
        if backup_directory is not None and (plan.changes or plan.cabinet_change):
            backup_directory.mkdir(parents=True, exist_ok=True)
            backup = (
                backup_directory / f"ozon-tax-history-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex}.json"
            )
            with backup.open("x", encoding="utf-8") as stream:
                backup.chmod(0o600)
                stream.write(plan.model_dump_json(indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            await service.apply(plan)
        print(
            json.dumps(
                {
                    "applied": backup_directory is not None,
                    "backup": str(backup) if backup else None,
                    "report": plan.report.model_dump(mode="json"),
                },
                ensure_ascii=False,
            )
        )
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True)
    parser.add_argument("--through", required=True, type=date.fromisoformat)
    parser.add_argument("--vat-percent", required=True, type=Decimal)
    parser.add_argument("--usn-percent", required=True, type=Decimal)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-directory", type=Path)
    args = parser.parse_args()
    if args.apply and args.backup_directory is None:
        parser.error("--apply requires --backup-directory")
    command = OzonTaxHistoryCommand(
        store=args.store,
        through=args.through,
        vat_percent=args.vat_percent,
        usn_percent=args.usn_percent,
        operation=args.operation,
        reason=args.reason,
    )
    asyncio.run(run(command, args.backup_directory if args.apply else None))


if __name__ == "__main__":
    main()
