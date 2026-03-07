"""Core booking business logic (alias->URL based).

This module does not parse chat commands. It only implements booking rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from app.booking_time import format_remaining_time, parse_booking_duration_to_timedelta
from app.config import BotConfig, DEFAULT_CONFIG_PATH, EnvironmentConfig, load_config
from app.storage import HealthResultRepository

BookingAction = Literal["book", "status", "unbook", "unbook_all"]
BookingOutcome = Literal[
    "booked",
    "busy",
    "free",
    "unbooked",
    "unbooked_all",
    "incorrect_alias",
    "incorrect_or_missing_time",
]


@dataclass(slots=True)
class BookingServiceResult:
    """Structured booking operation result for command handlers."""

    action: BookingAction
    outcome: BookingOutcome
    alias: str
    url: str | None
    env_name: str | None
    username: str | None = None
    booked_at: str | None = None
    booked_until: str | None = None
    remaining_time: str | None = None
    affected_count: int | None = None
    error_message: str | None = None

    @property
    def is_error(self) -> bool:
        return self.outcome in {"incorrect_alias", "incorrect_or_missing_time"}


@dataclass(slots=True)
class ResolvedEnvironment:
    alias: str
    url: str
    env_name: str


class BookingService:
    """Booking operations keyed by environment URL."""

    def __init__(
        self,
        *,
        repository: HealthResultRepository,
        environments: dict[str, EnvironmentConfig],
    ) -> None:
        self.repository = repository
        self.environments = dict(environments)

    @classmethod
    def from_config(
        cls,
        config: BotConfig,
        *,
        repository: HealthResultRepository | None = None,
    ) -> BookingService:
        repo = repository or HealthResultRepository(config.database.sqlite_path)
        return cls(repository=repo, environments=config.environments)

    @classmethod
    def from_config_file(
        cls,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        *,
        repository: HealthResultRepository | None = None,
    ) -> BookingService:
        config = load_config(config_path)
        return cls.from_config(config, repository=repository)

    async def book(self, *, alias: str, username: str, duration: str) -> BookingServiceResult:
        resolved = self._resolve_alias(alias)
        if resolved is None:
            return BookingServiceResult(
                action="book",
                outcome="incorrect_alias",
                alias=alias,
                url=None,
                env_name=None,
                username=username,
                error_message=f"Unknown environment alias: {alias}",
            )

        normalized_username = _normalize_required(username, field_name="username")
        try:
            duration_delta = parse_booking_duration_to_timedelta(duration)
        except ValueError as exc:
            return BookingServiceResult(
                action="book",
                outcome="incorrect_or_missing_time",
                alias=resolved.alias,
                url=resolved.url,
                env_name=resolved.env_name,
                username=normalized_username,
                error_message=str(exc),
            )

        now_dt = _now_local_datetime()
        now_iso = _to_iso_seconds(now_dt)
        booked_until_dt = now_dt + duration_delta
        booked_until_iso = _to_iso_seconds(booked_until_dt)
        remaining = format_remaining_time(now_dt, booked_until_dt)

        current = await self.repository.get_booking_current(resolved.url)
        if self._is_active_booking(current, now_dt):
            assert current is not None
            holder = (current.username or "").strip()
            if holder and holder != normalized_username:
                until_dt = self._parse_booked_until(current.booked_until, now_dt) or now_dt
                remaining = format_remaining_time(now_dt, until_dt)
                return BookingServiceResult(
                    action="book",
                    outcome="busy",
                    alias=resolved.alias,
                    url=resolved.url,
                    env_name=resolved.env_name,
                    username=holder,
                    booked_at=current.booked_at,
                    booked_until=current.booked_until,
                    remaining_time=remaining,
                    error_message=None,
                )

            # Extend/refresh same-user booking: keep existing username when present.
            effective_username = holder or normalized_username
            await self.repository.set_booking_booked(
                url=resolved.url,
                username=effective_username,
                booked_at=now_iso,
                booked_until=booked_until_iso,
            )
            await self.repository.insert_booking_history(
                username=normalized_username,
                url=resolved.url,
                action_status="booked",
                action_time=now_iso,
            )
            return BookingServiceResult(
                action="book",
                outcome="booked",
                alias=resolved.alias,
                url=resolved.url,
                env_name=resolved.env_name,
                username=effective_username,
                booked_at=now_iso,
                booked_until=booked_until_iso,
                remaining_time=remaining,
            )

        # No active booking (including expired): write current state + booked history.
        await self.repository.set_booking_booked(
            url=resolved.url,
            username=normalized_username,
            booked_at=now_iso,
            booked_until=booked_until_iso,
        )
        await self.repository.insert_booking_history(
            username=normalized_username,
            url=resolved.url,
            action_status="booked",
            action_time=now_iso,
        )
        return BookingServiceResult(
            action="book",
            outcome="booked",
            alias=resolved.alias,
            url=resolved.url,
            env_name=resolved.env_name,
            username=normalized_username,
            booked_at=now_iso,
            booked_until=booked_until_iso,
            remaining_time=remaining,
        )

    async def book_status(self, *, alias: str) -> BookingServiceResult:
        resolved = self._resolve_alias(alias)
        if resolved is None:
            return BookingServiceResult(
                action="status",
                outcome="incorrect_alias",
                alias=alias,
                url=None,
                env_name=None,
                error_message=f"Unknown environment alias: {alias}",
            )

        now_dt = _now_local_datetime()
        current = await self.repository.get_booking_current(resolved.url)

        if current is None:
            return BookingServiceResult(
                action="status",
                outcome="free",
                alias=resolved.alias,
                url=resolved.url,
                env_name=resolved.env_name,
            )

        if self._is_active_booking(current, now_dt):
            until_dt = self._parse_booked_until(current.booked_until, now_dt) or now_dt
            remaining = format_remaining_time(now_dt, until_dt)
            return BookingServiceResult(
                action="status",
                outcome="booked",
                alias=resolved.alias,
                url=resolved.url,
                env_name=resolved.env_name,
                username=current.username,
                booked_at=current.booked_at,
                booked_until=current.booked_until,
                remaining_time=remaining,
            )

        if current.status == "booked":
            # Expired booking: cleanup current row only (no unbooked history).
            await self.repository.set_booking_free(
                url=resolved.url,
            )

        return BookingServiceResult(
            action="status",
            outcome="free",
            alias=resolved.alias,
            url=resolved.url,
            env_name=resolved.env_name,
        )

    async def unbook(self, *, alias: str, username: str) -> BookingServiceResult:
        resolved = self._resolve_alias(alias)
        if resolved is None:
            return BookingServiceResult(
                action="unbook",
                outcome="incorrect_alias",
                alias=alias,
                url=None,
                env_name=None,
                username=username,
                error_message=f"Unknown environment alias: {alias}",
            )

        normalized_username = _normalize_required(username, field_name="username")
        now_iso = _to_iso_seconds(_now_local_datetime())

        await self.repository.set_booking_free(
            url=resolved.url,
        )
        await self.repository.insert_booking_history(
            username=normalized_username,
            url=resolved.url,
            action_status="unbooked",
            action_time=now_iso,
        )

        return BookingServiceResult(
            action="unbook",
            outcome="unbooked",
            alias=resolved.alias,
            url=resolved.url,
            env_name=resolved.env_name,
            username=normalized_username,
        )

    async def unbook_all(self, *, username: str) -> BookingServiceResult:
        """Clear booking state for all rows and write `unbooked` history for cleared bookings."""
        normalized_username = _normalize_required(username, field_name="username")
        now_iso = _to_iso_seconds(_now_local_datetime())

        current_rows = await self.repository.list_booking_current()
        booked_urls = [row.url for row in current_rows if row.status == "booked"]

        await self.repository.set_all_bookings_free()

        for url in booked_urls:
            await self.repository.insert_booking_history(
                username=normalized_username,
                url=url,
                action_status="unbooked",
                action_time=now_iso,
            )

        return BookingServiceResult(
            action="unbook_all",
            outcome="unbooked_all",
            alias="all",
            url=None,
            env_name="all",
            username=normalized_username,
            affected_count=len(booked_urls),
        )

    def _resolve_alias(self, alias: str) -> ResolvedEnvironment | None:
        normalized_alias = alias.strip()
        if not normalized_alias:
            return None

        env = self.environments.get(normalized_alias)
        if env is None:
            return None

        return ResolvedEnvironment(
            alias=normalized_alias,
            url=env.url,
            env_name=env.name or normalized_alias,
        )

    @staticmethod
    def _is_active_booking(current: object, now_dt: datetime) -> bool:
        if current is None:
            return False

        status = getattr(current, "status", None)
        if status != "booked":
            return False

        raw_until = getattr(current, "booked_until", None)
        if not isinstance(raw_until, str) or not raw_until.strip():
            return False

        booked_until_dt = BookingService._parse_booked_until(raw_until, now_dt)
        if booked_until_dt is None:
            return False

        return booked_until_dt > now_dt

    @staticmethod
    def _parse_booked_until(raw_until: object, now_dt: datetime) -> datetime | None:
        if not isinstance(raw_until, str) or not raw_until.strip():
            return None

        try:
            booked_until_dt = datetime.fromisoformat(raw_until.strip())
        except ValueError:
            return None

        if booked_until_dt.tzinfo is None:
            booked_until_dt = booked_until_dt.replace(tzinfo=now_dt.tzinfo)
        return booked_until_dt


def _normalize_required(value: str, *, field_name: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized


def _now_local_datetime() -> datetime:
    # Keep the same source/style as storage fetched_at: local timezone + second precision.
    return datetime.now().astimezone().replace(microsecond=0)


def _to_iso_seconds(value: datetime) -> str:
    return value.isoformat(timespec="seconds")
