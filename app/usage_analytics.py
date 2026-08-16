from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import MOSCOW_TIMEZONE
from app.dto.identity import SectionName
from app.infrastructure.orm import (
    UsageSessionRecord,
    UserRecord,
    UserSectionUsageRecord,
)

ONLINE_TIMEOUT = timedelta(minutes=10)
MAX_HEARTBEAT_DELTA_SECONDS = 120


def _session_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class UsageAnalyticsService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def start_session(self, token: str, user_id: int) -> None:
        now = self._clock()
        with self._session_factory() as session:
            record = session.scalar(
                select(UsageSessionRecord).where(UsageSessionRecord.session_key == _session_key(token))
            )
            if record is None:
                session.add(
                    UsageSessionRecord(
                        session_key=_session_key(token),
                        user_id=user_id,
                        started_at=now.isoformat(),
                        last_seen_at=now.isoformat(),
                        active_seconds=0,
                    )
                )
            session.commit()

    def end_session(self, token: str) -> None:
        now = self._clock()
        with self._session_factory() as session:
            record = session.scalar(
                select(UsageSessionRecord).where(UsageSessionRecord.session_key == _session_key(token))
            )
            if record is not None and record.ended_at is None:
                record.ended_at = now.isoformat()
                record.idle_at = now.isoformat()
                session.commit()

    def heartbeat(
        self,
        token: str,
        user_id: int,
        section: SectionName | None,
        path: str,
        *,
        active: bool,
        page_view: bool,
    ) -> None:
        now = self._clock()
        now_iso = now.isoformat()
        with self._session_factory() as session:
            record = session.scalar(
                select(UsageSessionRecord).where(
                    UsageSessionRecord.session_key == _session_key(token),
                    UsageSessionRecord.user_id == user_id,
                )
            )
            if record is None:
                record = UsageSessionRecord(
                    session_key=_session_key(token),
                    user_id=user_id,
                    started_at=now_iso,
                    last_seen_at=now_iso,
                    active_seconds=0,
                )
                session.add(record)
                session.flush()

            previous_seen = _as_datetime(record.last_seen_at) or now
            elapsed = max(0, int((now - previous_seen).total_seconds()))
            delta = 0
            if record.ended_at is None and record.idle_at is None:
                if elapsed <= MAX_HEARTBEAT_DELTA_SECONDS:
                    delta = elapsed

            usage_section = section or (
                SectionName(record.last_section)
                if record.last_section in SectionName._value2member_map_
                else None
            )
            if active:
                record.last_seen_at = now_iso
                record.idle_at = None
                record.ended_at = None
                if section is not None:
                    record.last_section = section.value
                record.last_path = path[:500]
            elif record.idle_at is None:
                record.idle_at = now_iso

            if delta:
                record.active_seconds += delta
            if usage_section is not None and (delta or (page_view and active)):
                self._record_section_usage(
                    session,
                    user_id,
                    usage_section,
                    now,
                    active_seconds=delta,
                    page_view=page_view and active,
                )
            session.commit()

    @staticmethod
    def _record_section_usage(
        session: Session,
        user_id: int,
        section: SectionName,
        now: datetime,
        *,
        active_seconds: int,
        page_view: bool,
    ) -> None:
        usage_date = now.astimezone(MOSCOW_TIMEZONE).date().isoformat()
        key = (user_id, section.value, usage_date)
        record = session.get(UserSectionUsageRecord, key)
        if record is None:
            record = UserSectionUsageRecord(
                user_id=user_id,
                section=section.value,
                usage_date=usage_date,
                last_viewed_at=now.isoformat(),
                page_views=0,
                active_seconds=0,
            )
            session.add(record)
        record.active_seconds += active_seconds
        record.page_views += int(page_view)
        record.last_viewed_at = now.isoformat()

    def dashboard(self, days: int = 30) -> dict[str, object]:
        days = min(365, max(1, int(days)))
        now = self._clock()
        local_today = now.astimezone(MOSCOW_TIMEZONE).date()
        start_date = (local_today - timedelta(days=days - 1)).isoformat()
        today = local_today.isoformat()
        online_cutoff = now - ONLINE_TIMEOUT

        with self._session_factory() as session:
            users = list(session.scalars(select(UserRecord).order_by(UserRecord.full_name)))
            usage_rows = list(
                session.scalars(
                    select(UserSectionUsageRecord).where(UserSectionUsageRecord.usage_date >= start_date)
                )
            )
            recent_sessions = list(
                session.scalars(select(UsageSessionRecord).order_by(UsageSessionRecord.id.desc()).limit(500))
            )

        user_by_id = {user.id: user for user in users}
        usage_by_user: dict[int, dict[str, int]] = defaultdict(
            lambda: {"period": 0, "today": 0, "views": 0, "views_today": 0}
        )
        section_totals: dict[str, dict[str, object]] = defaultdict(
            lambda: {"active_seconds": 0, "page_views": 0, "users": set()}
        )
        for row in usage_rows:
            usage_by_user[row.user_id]["period"] += row.active_seconds
            usage_by_user[row.user_id]["views"] += row.page_views
            if row.usage_date == today:
                usage_by_user[row.user_id]["today"] += row.active_seconds
                usage_by_user[row.user_id]["views_today"] += row.page_views
            section_totals[row.section]["active_seconds"] += row.active_seconds
            section_totals[row.section]["page_views"] += row.page_views
            section_totals[row.section]["users"].add(row.user_id)

        latest_by_user: dict[int, UsageSessionRecord] = {}
        session_count: dict[int, int] = defaultdict(int)
        period_start = now - timedelta(days=days)
        for usage_session in recent_sessions:
            latest_by_user.setdefault(usage_session.user_id, usage_session)
            started_at = _as_datetime(usage_session.started_at)
            if started_at is not None and started_at >= period_start:
                session_count[usage_session.user_id] += 1

        people = []
        for user in users:
            latest = latest_by_user.get(user.id)
            last_seen = _as_datetime(latest.last_seen_at) if latest else None
            online = bool(
                latest
                and latest.ended_at is None
                and latest.idle_at is None
                and last_seen is not None
                and last_seen >= online_cutoff
            )
            people.append(
                {
                    "user_id": user.id,
                    "full_name": user.full_name,
                    "login": user.login,
                    "role": user.role,
                    "online": online,
                    "last_login": latest.started_at if latest else None,
                    "last_seen": latest.last_seen_at if latest else None,
                    "last_section": latest.last_section if latest else None,
                    "last_path": latest.last_path if latest else None,
                    "active_today": usage_by_user[user.id]["today"],
                    "active_period": usage_by_user[user.id]["period"],
                    "page_views": usage_by_user[user.id]["views"],
                    "session_count": session_count[user.id],
                }
            )
        people.sort(key=lambda item: (not item["online"], item["full_name"].casefold()))

        sections = [
            {
                "section": section,
                "active_seconds": int(values["active_seconds"]),
                "page_views": int(values["page_views"]),
                "unique_users": len(values["users"]),
            }
            for section, values in section_totals.items()
        ]
        sections.sort(key=lambda item: (item["active_seconds"], item["page_views"]), reverse=True)

        sessions = []
        for usage_session in recent_sessions[:100]:
            user = user_by_id.get(usage_session.user_id)
            last_seen = _as_datetime(usage_session.last_seen_at)
            online = bool(
                usage_session.ended_at is None
                and usage_session.idle_at is None
                and last_seen is not None
                and last_seen >= online_cutoff
            )
            sessions.append(
                {
                    "full_name": user.full_name if user else f"Сотрудник #{usage_session.user_id}",
                    "login": user.login if user else "—",
                    "started_at": usage_session.started_at,
                    "last_seen_at": usage_session.last_seen_at,
                    "ended_at": usage_session.ended_at,
                    "active_seconds": usage_session.active_seconds,
                    "last_section": usage_session.last_section,
                    "last_path": usage_session.last_path,
                    "online": online,
                }
            )

        return {
            "days": days,
            "generated_at": now.isoformat(),
            "online_count": sum(1 for person in people if person["online"]),
            "active_today_count": sum(
                1
                for person in people
                if person["active_today"] > 0 or usage_by_user[person["user_id"]]["views_today"] > 0
            ),
            "active_today_seconds": sum(person["active_today"] for person in people),
            "period_page_views": sum(person["page_views"] for person in people),
            "people": people,
            "sections": sections,
            "sessions": sessions,
        }
