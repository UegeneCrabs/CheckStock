from datetime import UTC, datetime


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _fmt_num(value: int | None) -> str:
    if value is None:
        return "0"
    return f"{value:,}".replace(",", " ")


def _cell(value: int | None) -> str:

    return _fmt_num(value) if value else "—"
