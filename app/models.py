"""Lightweight data models used across the bot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

HealthStatus = Literal["success", "error"]
BookingStatus = Literal["free", "booked"]
BookingActionStatus = Literal["booked", "unbooked"]


@dataclass(slots=True)
class HealthResult:
    """Result payload persisted for one `!hc version <alias>` execution."""

    alias: str
    url: str
    branch: str | None = None
    commit_hash: str | None = None
    tag: str | None = None
    hc_timestamp: str | None = None
    fetched_at: str | None = None
    status: HealthStatus = "success"
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ("success", "error"):
            raise ValueError("status must be 'success' or 'error'")
        if not self.alias.strip():
            raise ValueError("alias must be a non-empty string")
        if not self.url.strip():
            raise ValueError("url must be a non-empty string")


@dataclass(slots=True)
class StoredHealthResult(HealthResult):
    """Health result read back from SQLite with database ID."""

    id: int = 0


@dataclass(slots=True)
class BookingCurrentRecord:
    """Current booking state for one environment URL."""

    url: str
    username: str | None = None
    status: BookingStatus = "free"
    booked_at: str | None = None
    booked_until: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ("free", "booked"):
            raise ValueError("status must be 'free' or 'booked'")
        if not self.url.strip():
            raise ValueError("url must be a non-empty string")


@dataclass(slots=True)
class BookingHistoryRecord:
    """One successful booking action event."""

    username: str
    url: str
    action_status: BookingActionStatus
    action_time: str
    id: int = 0

    def __post_init__(self) -> None:
        if self.action_status not in ("booked", "unbooked"):
            raise ValueError("action_status must be 'booked' or 'unbooked'")
        if not self.username.strip():
            raise ValueError("username must be a non-empty string")
        if not self.url.strip():
            raise ValueError("url must be a non-empty string")
