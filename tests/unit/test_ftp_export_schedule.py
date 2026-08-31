from datetime import UTC, datetime

from app import ftp_export_schedule
from app.domain import MOSCOW_TIMEZONE


def _moscow(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 31, hour, minute, tzinfo=MOSCOW_TIMEZONE)


def test_startup_runs_immediately_inside_retry_window(monkeypatch) -> None:
    monkeypatch.setattr(ftp_export_schedule.db, "list_sync_job_states", lambda: [])

    assert ftp_export_schedule.startup_delay_seconds(_moscow(2)) == 75 * 60
    assert ftp_export_schedule.startup_delay_seconds(_moscow(4)) == 0
    assert ftp_export_schedule.should_attempt("ftp_wb_export", _moscow(4)) is True


def test_failed_export_retries_until_deadline(monkeypatch) -> None:
    monkeypatch.setattr(ftp_export_schedule.db, "list_sync_job_states", lambda: [])

    assert ftp_export_schedule.next_delay_seconds("ftp_wb_export", _moscow(4)) == 20 * 60
    assert ftp_export_schedule.should_attempt("ftp_wb_export", _moscow(5, 59)) is True
    assert ftp_export_schedule.should_attempt("ftp_wb_export", _moscow(6)) is False
    assert ftp_export_schedule.next_delay_seconds("ftp_wb_export", _moscow(5, 50)) > 20 * 60


def test_success_in_current_window_suppresses_further_attempts(monkeypatch) -> None:
    success_at = datetime(2026, 8, 31, 0, 20, tzinfo=UTC).isoformat()
    monkeypatch.setattr(
        ftp_export_schedule.db,
        "list_sync_job_states",
        lambda: [{"name": "ftp_ozon_export", "last_success_at": success_at}],
    )

    assert ftp_export_schedule.succeeded_in_current_window("ftp_ozon_export", _moscow(4)) is True
    assert ftp_export_schedule.should_attempt("ftp_ozon_export", _moscow(4)) is False
    assert ftp_export_schedule.next_delay_seconds("ftp_ozon_export", _moscow(4)) == 23.25 * 60 * 60


def test_success_before_window_does_not_replace_nightly_export(monkeypatch) -> None:
    success_at = datetime(2026, 8, 30, 23, 0, tzinfo=UTC).isoformat()
    monkeypatch.setattr(
        ftp_export_schedule.db,
        "list_sync_job_states",
        lambda: [{"name": "ftp_wb_export", "last_success_at": success_at}],
    )

    assert ftp_export_schedule.should_attempt("ftp_wb_export", _moscow(3, 15)) is True
