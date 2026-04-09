"""Shared timestamp conversion utilities."""

from datetime import UTC, date, datetime


def to_timestamp_ms(d: str | date) -> int:
    """Convert a date string or date object to milliseconds timestamp (noon UTC).

    Args:
        d: ISO-format date string (e.g. "2024-01-15") or a date object

    Returns:
        Unix timestamp in milliseconds (at noon UTC on the given date)
    """
    if isinstance(d, str):
        d = date.fromisoformat(d)
    dt = datetime(d.year, d.month, d.day, hour=12, tzinfo=UTC)
    return int(dt.timestamp() * 1000)
