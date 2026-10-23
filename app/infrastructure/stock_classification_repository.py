from sqlalchemy import and_, or_, select

from app.core.constants import StockOperationFilter, WebErrorCode
from app.dto.stock_pages import StockClassificationCommand, StockClassificationResult, StockPageQuery
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.orm import StockOperationRecord, StockOperationReportFlagRecord


class StockClassificationRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def classify(
        self, scope: StockPageQuery, command: StockClassificationCommand
    ) -> StockClassificationResult:
        operations = table_for(StockOperationRecord)
        flags = table_for(StockOperationReportFlagRecord)
        allowed = or_(
            *(
                and_(
                    operations.c.store_slug == target.store_slug,
                    or_(
                        operations.c.from_marketplace == target.marketplace,
                        and_(
                            or_(operations.c.from_marketplace.is_(None), operations.c.from_marketplace == ""),
                            operations.c.to_marketplace == target.marketplace,
                        ),
                    ),
                )
                for target in scope.targets
            )
        )
        async with self.database.transaction(write=True) as connection:
            operation = (
                await connection.execute(
                    select(operations.c.kind)
                    .where(operations.c.id == command.operation_id, allowed)
                    .with_for_update()
                )
            ).first()
            if operation is None:
                raise ValueError(WebErrorCode.NOT_FOUND)
            if operation.kind != StockOperationFilter.SHIPMENT:
                raise ValueError(WebErrorCode.INVALID_TARGET)
            await connection.execute(
                upsert(
                    connection,
                    flags,
                    {
                        "operation_id": command.operation_id,
                        "is_fbs_transfer": int(command.is_fbs_transfer),
                        "updated_by": command.user_id,
                        "updated_at": command.updated_at,
                    },
                    ("operation_id",),
                )
            )
        return StockClassificationResult(
            operation_id=command.operation_id, is_fbs_transfer=command.is_fbs_transfer
        )
