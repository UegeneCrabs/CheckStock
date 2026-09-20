"""Report quota deadlines shared by workers and retained through application reloads."""

from datetime import UTC, datetime

from app.repositories.core import WRITE_LOCK, get_connection


def remaining(business_id: int, report: str) -> float:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT next_request_at FROM yandex_report_limits WHERE business_id=? AND report=?",
            (str(business_id), report),
        ).fetchone()
    if not row:
        return 0.0
    deadline = datetime.fromisoformat(row["next_request_at"])
    return max(0.0, (deadline - datetime.now(UTC)).total_seconds())


def defer(business_id: int, report: str, deadline: datetime) -> None:
    with WRITE_LOCK, get_connection() as connection:
        connection.execute(
            "INSERT INTO yandex_report_limits (business_id,report,next_request_at) VALUES (?,?,?) "
            "ON CONFLICT(business_id,report) DO UPDATE SET next_request_at=excluded.next_request_at "
            "WHERE yandex_report_limits.next_request_at < excluded.next_request_at",
            (str(business_id), report, deadline.astimezone(UTC).isoformat(timespec="microseconds")),
        )
        connection.commit()
