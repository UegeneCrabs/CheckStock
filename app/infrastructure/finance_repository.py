"""SQLAlchemy persistence with explicit scope and atomic publication."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.orm import load_only

from app.dto.finance import FinanceEvent, SourceBatch
from app.finance.calculation import VERSION
from app.infrastructure.finance_orm import (
    FinanceAttempt,
    FinanceConnection,
    FinanceCost,
    FinanceHead,
    FinanceOperation,
    FinanceSnapshot,
    FinanceTicket,
    FinanceUnallocated,
)


def now():
    return datetime.now(UTC).isoformat()


def connection_dict(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "store_slug",
            "business_id",
            "campaign_ids",
            "effective_from",
            "effective_to",
            "active",
            "created_at",
            "actor",
        )
    }


class FinanceRepository:
    def __init__(self, session_factory):
        self.sessions = session_factory

    def connections(self, stores: tuple[str, ...]):
        if not stores:
            return []
        with self.sessions() as session:
            return [
                connection_dict(row)
                for row in session.scalars(
                    select(FinanceConnection)
                    .where(FinanceConnection.store_slug.in_(stores))
                    .order_by(FinanceConnection.created_at)
                )
            ]

    def add_connection(self, request, actor, *, identifier=None):
        with self.sessions.begin() as session:
            others = session.scalars(
                select(FinanceConnection).where(FinanceConnection.business_id == request.business_id)
            )
            for other in others:
                # Disabled connections still own their historical interval.
                if (
                    set(other.campaign_ids) & set(request.campaign_ids)
                    and other.effective_from <= (request.effective_to or date.max)
                    and request.effective_from <= (other.effective_to or date.max)
                ):
                    raise ValueError("Кампания уже привязана на пересекающийся период")
            row = FinanceConnection(
                id=identifier or str(uuid4()),
                store_slug=request.store_slug,
                business_id=request.business_id,
                campaign_ids=request.campaign_ids,
                effective_from=request.effective_from,
                effective_to=request.effective_to,
                active=True,
                actor=actor,
                created_at=now(),
            )
            session.add(row)
            session.flush()
            return connection_dict(row)

    def disable(self, identifier, end):
        with self.sessions.begin() as session:
            row = session.get(FinanceConnection, identifier)
            if row is None:
                raise ValueError("Подключение не найдено")
            if end < row.effective_from:
                raise ValueError("Дата отключения раньше начала подключения")
            latest = session.scalar(
                select(FinanceOperation.day)
                .join(FinanceSnapshot)
                .where(FinanceSnapshot.connection_id == identifier)
                .order_by(FinanceOperation.day.desc())
                .limit(1)
            )
            if latest and end < latest:
                raise ValueError(
                    "Нельзя обрезать опубликованную историю: выберите дату не раньше последней операции"
                )
            row.active, row.effective_to = False, end

    def publish(self, connection, batches: list[SourceBatch], actor):
        """All sources supplied by the caller become visible in one transaction."""
        if len({(b.source, b.start) for b in batches}) != len(batches):
            raise ValueError("Повтор источника в публикации")
        ids = []
        with self.sessions.begin() as session:
            persisted = session.get(FinanceConnection, connection["id"])
            if persisted is None:
                raise ValueError("Подключение не найдено")
            for batch in batches:
                old_head = session.get(FinanceHead, (connection["id"], batch.source, batch.start))
                if old_head and session.get(FinanceSnapshot, old_head.snapshot_id).end > batch.end:
                    raise ValueError("Новая загрузка не может сократить опубликованное покрытие месяца")
                if any(e.campaign_id and e.campaign_id not in persisted.campaign_ids for e in batch.events):
                    raise ValueError("Операция чужой кампании")
                stamp = now()
                payload = batch.model_dump(mode="json")
                snapshot = FinanceSnapshot(
                    connection_id=connection["id"],
                    source=batch.source,
                    start=batch.start,
                    end=batch.end,
                    raw=batch.raw,
                    report_ids=list(batch.report_ids),
                    issues=list(batch.issues),
                    income_issues=list(batch.income_issues),
                    captured_at=stamp,
                    formula_version=VERSION,
                    actor=actor,
                    fingerprint=hashlib.sha256(
                        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
                    ).hexdigest(),
                )
                session.add(snapshot)
                session.flush()
                session.execute(
                    delete(FinanceUnallocated).where(
                        FinanceUnallocated.business_id == persisted.business_id,
                        FinanceUnallocated.source == batch.source,
                        FinanceUnallocated.month == batch.start,
                    )
                )
                for unallocated in batch.unallocated:
                    session.add(
                        FinanceUnallocated(
                            business_id=persisted.business_id,
                            source=batch.source,
                            month=batch.start,
                            key=unallocated.key,
                            payload=unallocated.model_dump(mode="json"),
                            captured_at=stamp,
                        )
                    )
                for event in batch.events:
                    if event.day < persisted.effective_from or (
                        persisted.effective_to and event.day > persisted.effective_to
                    ):
                        continue
                    session.add(
                        FinanceOperation(
                            snapshot_id=snapshot.id,
                            key=event.key,
                            day=event.day,
                            payload=event.model_dump(mode="json"),
                        )
                    )
                head = session.get(FinanceHead, (connection["id"], batch.source, batch.start))
                if head is None:
                    session.add(
                        FinanceHead(
                            connection_id=connection["id"],
                            source=batch.source,
                            month=batch.start,
                            snapshot_id=snapshot.id,
                        )
                    )
                else:
                    head.snapshot_id = snapshot.id
                session.merge(
                    FinanceAttempt(
                        connection_id=connection["id"],
                        source=batch.source,
                        month=batch.start,
                        attempted_at=stamp,
                        error="",
                        state="success",
                    )
                )
                ids.append(snapshot.id)
        return ids

    def failed(self, connection_id, month, sources, error, state="error"):
        with self.sessions.begin() as session:
            for source in sources:
                session.merge(
                    FinanceAttempt(
                        connection_id=connection_id,
                        source=source,
                        month=month,
                        attempted_at=now(),
                        error=error,
                        state=state,
                    )
                )

    def read(self, stores, start, end):
        """Select immutable revision ids first, then read exactly these revisions."""
        if not stores:
            return {"connections": [], "snapshots": [], "events": [], "attempts": []}
        with self.sessions() as session:
            connections = [
                connection_dict(row)
                for row in session.scalars(
                    select(FinanceConnection).where(
                        FinanceConnection.store_slug.in_(stores),
                        FinanceConnection.effective_from <= end,
                        (
                            FinanceConnection.effective_to.is_(None)
                            | (FinanceConnection.effective_to >= start)
                        ),
                    )
                )
            ]
            by_id = {c["id"]: c for c in connections}
            snapshots = list(
                session.scalars(
                    select(FinanceSnapshot)
                    .options(
                        load_only(
                            FinanceSnapshot.id,
                            FinanceSnapshot.connection_id,
                            FinanceSnapshot.source,
                            FinanceSnapshot.start,
                            FinanceSnapshot.end,
                            FinanceSnapshot.issues,
                            FinanceSnapshot.income_issues,
                            FinanceSnapshot.captured_at,
                            FinanceSnapshot.formula_version,
                        )
                    )
                    .join(FinanceHead, FinanceHead.snapshot_id == FinanceSnapshot.id)
                    .where(
                        FinanceHead.connection_id.in_(by_id),
                        FinanceHead.month >= start.replace(day=1),
                        FinanceHead.month <= end,
                    )
                )
            )
            snapshot_map = {s.id: s for s in snapshots}
            events = []
            # Chunk IN clauses for SQLite installations with a lower variable limit.
            identifiers = list(snapshot_map)
            for offset in range(0, len(identifiers), 300):
                for row in session.scalars(
                    select(FinanceOperation)
                    .where(
                        FinanceOperation.snapshot_id.in_(identifiers[offset : offset + 300]),
                        FinanceOperation.day >= start,
                        FinanceOperation.day <= end,
                    )
                    .order_by(FinanceOperation.day, FinanceOperation.snapshot_id, FinanceOperation.key)
                ):
                    snap = snapshot_map[row.snapshot_id]
                    conn = by_id[snap.connection_id]
                    events.append(
                        {
                            "event": FinanceEvent.model_validate(row.payload),
                            "snapshot_id": snap.id,
                            "store_slug": conn["store_slug"],
                            "business_id": conn["business_id"],
                            "connection_id": conn["id"],
                            "captured_at": snap.captured_at,
                        }
                    )
            attempts = [
                dict(
                    connection_id=a.connection_id,
                    month=a.month,
                    source=a.source,
                    attempted_at=a.attempted_at,
                    error=a.error,
                    state=a.state,
                )
                for a in session.scalars(
                    select(FinanceAttempt).where(
                        FinanceAttempt.connection_id.in_(by_id),
                        FinanceAttempt.month >= start.replace(day=1),
                        FinanceAttempt.month <= end,
                    )
                )
            ]
            return {
                "connections": connections,
                "events": events,
                "attempts": attempts,
                "snapshots": [
                    {
                        key: getattr(s, key)
                        for key in (
                            "id",
                            "connection_id",
                            "source",
                            "start",
                            "end",
                            "issues",
                            "income_issues",
                            "captured_at",
                            "formula_version",
                        )
                    }
                    for s in snapshots
                ],
            }

    def saved_batches(self, identifier, month):
        with self.sessions() as session:
            result = []
            for snap in session.scalars(
                select(FinanceSnapshot)
                .join(FinanceHead, FinanceHead.snapshot_id == FinanceSnapshot.id)
                .where(FinanceHead.connection_id == identifier, FinanceHead.month == month)
            ):
                result.append(
                    SourceBatch(
                        source=snap.source,
                        start=snap.start,
                        end=snap.end,
                        raw=snap.raw or {},
                        report_ids=tuple(snap.report_ids),
                        issues=tuple(snap.issues),
                        income_issues=tuple(snap.income_issues),
                        events=tuple(
                            FinanceEvent.model_validate(row.payload)
                            for row in session.scalars(
                                select(FinanceOperation).where(FinanceOperation.snapshot_id == snap.id)
                            )
                        ),
                    )
                )
            return result

    def unallocated(self):
        """Superadmin-only account view; never include in scoped store read models."""
        with self.sessions() as session:
            return [
                {
                    "business_id": r.business_id,
                    "month": str(r.month),
                    "captured_at": r.captured_at,
                    **r.payload,
                }
                for r in session.scalars(
                    select(FinanceUnallocated)
                    .order_by(FinanceUnallocated.month.desc(), FinanceUnallocated.business_id)
                    .limit(1000)
                )
            ]

    def add_cost(self, request, actor, origin="manual"):
        with self.sessions.begin() as session:
            row = FinanceCost(
                **request.model_dump(mode="python"), origin=origin, actor=actor, created_at=now()
            )
            row.price = str(request.price)
            session.add(row)
            session.flush()
            return row.id

    def costs(self, store):
        with self.sessions() as session:
            return [
                {
                    key: getattr(c, key)
                    for key in (
                        "id",
                        "store_slug",
                        "article",
                        "effective_from",
                        "effective_to",
                        "price",
                        "origin",
                        "actor",
                        "reason",
                        "created_at",
                    )
                }
                for c in session.scalars(
                    select(FinanceCost).where(FinanceCost.store_slug == store).order_by(FinanceCost.id)
                )
            ]

    def observe_costs(self, store, values):
        """Archive dated observations from YM without rewriting the existing source."""
        with self.sessions.begin() as session:
            for article, value in values.items():
                try:
                    price = Decimal(str(value["purchase_price"]))
                    from app.core.domain import MOSCOW_TIMEZONE

                    stamp = datetime.fromisoformat(str(value["synced_at"]))
                    observed = stamp.astimezone(MOSCOW_TIMEZONE).date() if stamp.tzinfo else stamp.date()
                    if not price.is_finite() or price < 0:
                        continue
                except (KeyError, ValueError, ArithmeticError):
                    continue
                found = session.scalar(
                    select(FinanceCost.id).where(
                        FinanceCost.store_slug == store,
                        FinanceCost.article == article,
                        FinanceCost.origin == "YM",
                        FinanceCost.created_at == str(value["synced_at"]),
                    )
                )
                if found is None:
                    session.add(
                        FinanceCost(
                            store_slug=store,
                            article=article,
                            price=str(price),
                            origin="YM",
                            actor="YM",
                            reason="Наблюдение импорта YM; до даты наблюдения цена неизвестна",
                            effective_from=observed,
                            effective_to=None,
                            created_at=str(value["synced_at"]),
                        )
                    )

    def ticket(self, key, report_id=None):
        with self.sessions.begin() as session:
            row = session.get(FinanceTicket, key)
            if report_id is None:
                return row.report_id if row else None
            if report_id == "":
                if row:
                    session.delete(row)
            else:
                session.merge(FinanceTicket(key=key, report_id=report_id, updated_at=now()))

    def trim_raw(self, retention_days):
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        with self.sessions.begin() as session:
            heads = select(FinanceHead.snapshot_id)
            # Retain facts and every currently published source; trim only superseded raw revisions.
            session.execute(
                update(FinanceSnapshot)
                .where(FinanceSnapshot.id.not_in(heads), FinanceSnapshot.captured_at < cutoff)
                .values(raw=None)
            )
            session.execute(delete(FinanceTicket).where(FinanceTicket.updated_at < cutoff))
