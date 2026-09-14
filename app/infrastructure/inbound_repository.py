import json
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from sqlalchemy import or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.inbound_supplies import Target
from app.dto.inbound_supplies import InboundSnapshot, InboundSupply
from app.infrastructure.orm import InboundSupplySnapshotRecord as SnapshotRecord
from app.infrastructure.orm import StockItemRecord


class SqlAlchemyInboundRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def read(self, targets: Sequence[Target]) -> list[InboundSnapshot]:
        if not targets:
            return []
        with self.session_factory() as session:
            rows = session.scalars(
                select(SnapshotRecord).where(
                    tuple_(SnapshotRecord.store_slug, SnapshotRecord.marketplace).in_(targets)
                )
            ).all()
            by_target = {(row.store_slug, row.marketplace): row for row in rows}
            result = []
            for store, marketplace in targets:
                row = by_target.get((store, marketplace))
                result.append(
                    InboundSnapshot(
                        store_slug=store,
                        marketplace=marketplace,
                        **(
                            {
                                "status": row.status,
                                "last_attempt": row.last_attempt,
                                "last_success": row.last_success,
                                "last_finished": row.last_finished,
                                "error": row.error,
                                "supplies": tuple(
                                    InboundSupply.model_validate(item)
                                    for item in json.loads(row.payload_json)
                                ),
                            }
                            if row
                            else {}
                        ),
                    )
                )
            return result

    def claim(self, target: Target, token: str, now: datetime) -> bool:
        with self.session_factory() as session:
            if session.get(SnapshotRecord, target) is None:
                session.add(SnapshotRecord(store_slug=target[0], marketplace=target[1]))
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
            result = session.execute(
                update(SnapshotRecord)
                .where(
                    SnapshotRecord.store_slug == target[0],
                    SnapshotRecord.marketplace == target[1],
                    or_(SnapshotRecord.run_token.is_(None), SnapshotRecord.lease_until < now.isoformat()),
                    or_(
                        SnapshotRecord.last_attempt.is_(None),
                        SnapshotRecord.last_attempt < (now - timedelta(seconds=60)).isoformat(),
                    ),
                )
                .values(
                    run_token=token,
                    lease_until=(now + timedelta(hours=2)).isoformat(),
                    last_attempt=now.isoformat(),
                    status="running",
                )
            )
            session.commit()
            return result.rowcount == 1

    def finish(
        self,
        target: Target,
        token: str,
        now: datetime,
        *,
        supplies: tuple[InboundSupply, ...] | None = None,
        status: str = "ok",
        error: str = "",
    ) -> bool:
        values = {
            "status": status,
            "error": error,
            "last_finished": now.isoformat(),
            "run_token": None,
            "lease_until": None,
        }
        if supplies is not None:
            values.update(
                payload_json=json.dumps(
                    [supply.model_dump(mode="json", exclude_computed_fields=True) for supply in supplies],
                    ensure_ascii=False,
                ),
                last_success=now.isoformat(),
            )
        with self.session_factory() as session:
            result = session.execute(
                update(SnapshotRecord)
                .where(
                    SnapshotRecord.store_slug == target[0],
                    SnapshotRecord.marketplace == target[1],
                    SnapshotRecord.run_token == token,
                )
                .values(**values)
            )
            session.commit()
            return result.rowcount == 1

    def enrich(self, target: Target, supplies: tuple[InboundSupply, ...]) -> tuple[InboundSupply, ...]:
        with self.session_factory() as session:
            catalog = session.scalars(
                select(StockItemRecord).where(
                    StockItemRecord.store_slug == target[0],
                    StockItemRecord.marketplace == target[1],
                )
            ).all()
            by_article = {row.article: row for row in catalog}
            by_barcode: dict[str, list] = {}
            for row in catalog:
                if row.barcode:
                    by_barcode.setdefault(row.barcode, []).append(row)
            result = []
            for supply in supplies:
                items = []
                for item in supply.items:
                    candidates = by_barcode.get(item.barcode, [])
                    product = (candidates[0] if len(candidates) == 1 else None) if target[1] == "WB" else None
                    product = product or by_article.get(item.article)
                    items.append(
                        item.model_copy(
                            update={
                                "article": product.article if product else item.article,
                                "name": item.name or (product.name if product else ""),
                                "barcode": item.barcode or (product.barcode if product else ""),
                            }
                        )
                    )
                result.append(supply.model_copy(update={"items": tuple(items)}))
            return tuple(result)
