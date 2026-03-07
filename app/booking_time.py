"""Booking duration parsing and remaining-time formatting helpers.

This module is intentionally independent from Rocket.Chat-specific code.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import re

_DURATION_RE = re.compile(r"^([1-9]\d*)([mhd])$")
_MIN_DURATION_MINUTES = 15
_MAX_DURATION_MINUTES = 7 * 24 * 60  # 10080m / 168h / 7d


def parse_booking_duration_to_minutes(raw: str) -> int:
    """Parse duration string (Xm/Xh/Xd) and return normalized minutes.

    Allowed formats:
    - Xm
    - Xh
    - Xd
    where X is a positive integer.
    """
    if raw is None or not raw.strip():
        raise ValueError("Missing booking duration. Expected format: Xm, Xh, or Xd.")

    normalized = raw.strip()
    match = _DURATION_RE.fullmatch(normalized)
    if match is None:
        raise ValueError(
            f"Invalid booking duration: {raw!r}. Expected format: Xm, Xh, or Xd."
        )

    value = int(match.group(1))
    unit = match.group(2)

    if unit == "m":
        minutes = value
    elif unit == "h":
        minutes = value * 60
    else:  # unit == "d"
        minutes = value * 24 * 60

    if minutes < _MIN_DURATION_MINUTES:
        raise ValueError("Booking duration is below minimum allowed: 15m.")

    if minutes > _MAX_DURATION_MINUTES:
        raise ValueError(
            "Booking duration exceeds maximum allowed: 10080m / 168h / 7d."
        )

    return minutes


def parse_booking_duration_to_timedelta(raw: str) -> timedelta:
    """Parse duration string and return normalized timedelta."""
    return timedelta(minutes=parse_booking_duration_to_minutes(raw))


def format_remaining_time(from_timestamp: datetime | str, to_timestamp: datetime | str) -> str:
    """Return remaining time text like '45min', '2h 5min', or '1d 3h'."""
    from_dt = _coerce_datetime(from_timestamp, field_name="from_timestamp")
    to_dt = _coerce_datetime(to_timestamp, field_name="to_timestamp")

    delta_seconds = int((to_dt - from_dt).total_seconds())
    return _format_remaining_from_seconds(delta_seconds)


def _coerce_datetime(value: datetime | str, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a datetime or non-empty ISO timestamp string")

    try:
        return datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be an ISO timestamp string parseable by datetime.fromisoformat()"
        ) from exc


def _format_remaining_from_seconds(total_seconds: int) -> str:
    if total_seconds <= 0:
        return "0min"

    # Round up positive partial minutes so 1..59s does not render as 0m.
    total_minutes = (total_seconds + 59) // 60

    days, remainder_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remainder_minutes, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}min")

    return " ".join(parts) if parts else "0min"
