"""SQLite repository for storing Rocket.Chat health command results.

Example SQL query for latest saved version by `_hc` URL:

    SELECT alias, branch, commit_hash, tag, hc_timestamp, fetched_at, status, error_message
    FROM hc_version_results
    WHERE url = ?
    ORDER BY fetched_at DESC, id DESC
    LIMIT 1;
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import aiosqlite

from app.models import (
    BookingActionStatus,
    BookingCurrentRecord,
    BookingHistoryRecord,
    BookingStatus,
    HealthResult,
    HealthStatus,
    StoredHealthResult,
)

CREATE_RESULTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS hc_version_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alias TEXT NOT NULL,
    url TEXT NOT NULL,
    branch TEXT,
    commit_hash TEXT,
    tag TEXT,
    hc_timestamp TEXT,
    fetched_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success', 'error')),
    error_message TEXT
);
"""

CREATE_RESULTS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_hc_version_results_alias_fetched_at
ON hc_version_results(alias, fetched_at DESC, id DESC);
"""

CREATE_RESULTS_URL_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_hc_version_results_url_fetched_at
ON hc_version_results(url, fetched_at DESC, id DESC);
"""

INSERT_RESULT_SQL = """
INSERT INTO hc_version_results (
    alias,
    url,
    branch,
    commit_hash,
    tag,
    hc_timestamp,
    fetched_at,
    status,
    error_message
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

UPDATE_RESULT_REFRESH_SQL = """
UPDATE hc_version_results
SET
    alias = ?,
    hc_timestamp = ?,
    fetched_at = ?
WHERE id = ?;
"""

SELECT_LAST_FOR_ALIAS_SQL = """
SELECT
    id,
    alias,
    url,
    branch,
    commit_hash,
    tag,
    hc_timestamp,
    fetched_at,
    status,
    error_message
FROM hc_version_results
WHERE alias = ?
ORDER BY fetched_at DESC, id DESC
LIMIT 1;
"""

SELECT_LAST_FOR_URL_SQL = """
SELECT
    id,
    alias,
    url,
    branch,
    commit_hash,
    tag,
    hc_timestamp,
    fetched_at,
    status,
    error_message
FROM hc_version_results
WHERE url = ?
ORDER BY fetched_at DESC, id DESC
LIMIT 1;
"""

CREATE_BOOKING_CURRENT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS booking_current (
    url TEXT PRIMARY KEY,
    username TEXT,
    status TEXT NOT NULL CHECK (status IN ('free', 'booked')),
    booked_at TEXT,
    booked_until TEXT
);
"""

CREATE_BOOKING_HISTORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS booking_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    url TEXT NOT NULL,
    action_status TEXT NOT NULL CHECK (action_status IN ('booked', 'unbooked')),
    action_time TEXT NOT NULL
);
"""

CREATE_BOOKING_HISTORY_URL_TIME_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_booking_history_url_action_time
ON booking_history(url, action_time DESC, id DESC);
"""

UPSERT_BOOKING_CURRENT_SQL = """
INSERT INTO booking_current (
    url,
    username,
    status,
    booked_at,
    booked_until
) VALUES (?, ?, ?, ?, ?)
ON CONFLICT(url) DO UPDATE SET
    username = excluded.username,
    status = excluded.status,
    booked_at = excluded.booked_at,
    booked_until = excluded.booked_until;
"""

SELECT_BOOKING_CURRENT_BY_URL_SQL = """
SELECT
    url,
    username,
    status,
    booked_at,
    booked_until
FROM booking_current
WHERE url = ?
LIMIT 1;
"""

SELECT_ALL_BOOKING_CURRENT_SQL = """
SELECT
    url,
    username,
    status,
    booked_at,
    booked_until
FROM booking_current
ORDER BY url ASC;
"""

UPDATE_ALL_BOOKING_CURRENT_FREE_SQL = """
UPDATE booking_current
SET
    username = NULL,
    status = 'free',
    booked_at = NULL,
    booked_until = NULL;
"""

INSERT_BOOKING_HISTORY_SQL = """
INSERT INTO booking_history (
    username,
    url,
    action_status,
    action_time
) VALUES (?, ?, ?, ?);
"""

SELECT_LAST_BOOKING_HISTORY_FOR_URL_SQL = """
SELECT
    id,
    username,
    url,
    action_status,
    action_time
FROM booking_history
WHERE url = ?
ORDER BY action_time DESC, id DESC
LIMIT 1;
"""


class HealthResultRepository:
    """Small repository layer around SQLite for health command results."""

    def __init__(self, sqlite_path: str | Path) -> None:
        self.sqlite_path = Path(sqlite_path)

    async def init_db(self) -> None:
        """Create database/table/index if missing."""
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self.sqlite_path) as conn:
            await conn.execute(CREATE_RESULTS_TABLE_SQL)
            await _migrate_commit_column_if_needed(conn)
            await conn.execute(CREATE_RESULTS_INDEX_SQL)
            await conn.execute(CREATE_RESULTS_URL_INDEX_SQL)
            await conn.execute(CREATE_BOOKING_CURRENT_TABLE_SQL)
            await conn.execute(CREATE_BOOKING_HISTORY_TABLE_SQL)
            await conn.execute(CREATE_BOOKING_HISTORY_URL_TIME_INDEX_SQL)
            await conn.commit()

    async def save_health_result(
        self,
        *,
        alias: str,
        url: str,
        branch: str | None,
        commit_hash: str | None,
        tag: str | None,
        hc_timestamp: str | None,
        status: HealthStatus,
        error_message: str | None = None,
        fetched_at: str | None = None,
    ) -> int:
        """Persist one result row.

        Deduplication/history grouping is based on `_hc` URL (not alias):
        - same meaningful state for same URL -> refresh latest row
        - changed meaningful state for same URL -> insert new row
        """
        result = HealthResult(
            alias=alias,
            url=url,
            branch=branch,
            commit_hash=commit_hash,
            tag=tag,
            hc_timestamp=hc_timestamp,
            fetched_at=fetched_at or _now_local_iso(),
            status=status,
            error_message=error_message,
        )

        async with aiosqlite.connect(self.sqlite_path) as conn:
            latest_for_url = await self._get_last_result_for_url_conn(conn, result.url)

            if latest_for_url and _is_same_meaningful_state(latest_for_url, result):
                await conn.execute(
                    UPDATE_RESULT_REFRESH_SQL,
                    (
                        result.alias,
                        result.hc_timestamp,
                        result.fetched_at,
                        latest_for_url.id,
                    ),
                )
                await conn.commit()
                return latest_for_url.id

            cursor = await conn.execute(
                INSERT_RESULT_SQL,
                (
                    result.alias,
                    result.url,
                    result.branch,
                    result.commit_hash,
                    result.tag,
                    result.hc_timestamp,
                    result.fetched_at,
                    result.status,
                    result.error_message,
                ),
            )
            await conn.commit()
            return int(cursor.lastrowid)

    async def get_last_result_for_url(self, url: str) -> StoredHealthResult | None:
        """Return most recent saved result for exact `_hc` URL, or None if missing."""
        if not url.strip():
            raise ValueError("url must be a non-empty string")

        async with aiosqlite.connect(self.sqlite_path) as conn:
            return await self._get_last_result_for_url_conn(conn, url)

    async def get_last_result_for_alias(self, alias: str) -> StoredHealthResult | None:
        """Return the most recent saved result for alias, or None if missing."""
        if not alias.strip():
            raise ValueError("alias must be a non-empty string")

        async with aiosqlite.connect(self.sqlite_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(SELECT_LAST_FOR_ALIAS_SQL, (alias,))
            row = await cursor.fetchone()

        if row is None:
            return None

        data = dict(row)
        return StoredHealthResult(**data)

    async def get_booking_current(self, url: str) -> BookingCurrentRecord | None:
        """Return current booking state for URL, or None if no row exists yet."""
        if not url.strip():
            raise ValueError("url must be a non-empty string")

        async with aiosqlite.connect(self.sqlite_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(SELECT_BOOKING_CURRENT_BY_URL_SQL, (url,))
            row = await cursor.fetchone()

        if row is None:
            return None

        return BookingCurrentRecord(**dict(row))

    async def list_booking_current(self) -> list[BookingCurrentRecord]:
        """Return all current booking rows."""
        async with aiosqlite.connect(self.sqlite_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(SELECT_ALL_BOOKING_CURRENT_SQL)
            rows = await cursor.fetchall()

        return [BookingCurrentRecord(**dict(row)) for row in rows]

    async def upsert_booking_current(
        self,
        *,
        url: str,
        status: BookingStatus,
        username: str | None = None,
        booked_at: str | None = None,
        booked_until: str | None = None,
    ) -> None:
        """Upsert one current booking row by URL.

        This writes only current-state data. It does not write `booking_history`.
        """
        if not url.strip():
            raise ValueError("url must be a non-empty string")
        if status not in ("free", "booked"):
            raise ValueError("status must be 'free' or 'booked'")

        if status == "free":
            username = None
            booked_at = None
            booked_until = None
        else:
            if username is None or not username.strip():
                raise ValueError("username must be a non-empty string when status is 'booked'")
            if booked_at is None:
                booked_at = _now_local_iso()

        async with aiosqlite.connect(self.sqlite_path) as conn:
            await conn.execute(
                UPSERT_BOOKING_CURRENT_SQL,
                (url, username, status, booked_at, booked_until),
            )
            await conn.commit()

    async def set_booking_booked(
        self,
        *,
        url: str,
        username: str,
        booked_at: str,
        booked_until: str,
    ) -> None:
        """Set current booking state to booked for URL (upsert semantics)."""
        await self.upsert_booking_current(
            url=url,
            status="booked",
            username=username,
            booked_at=booked_at,
            booked_until=booked_until,
        )

    async def set_booking_free(self, *, url: str) -> None:
        """Set current booking state to free for URL (upsert semantics)."""
        await self.upsert_booking_current(
            url=url,
            status="free",
        )

    async def set_all_bookings_free(self) -> int:
        """Set all current booking rows to free and clear booking fields."""
        async with aiosqlite.connect(self.sqlite_path) as conn:
            cursor = await conn.execute(UPDATE_ALL_BOOKING_CURRENT_FREE_SQL)
            await conn.commit()
            return int(cursor.rowcount or 0)

    async def insert_booking_history(
        self,
        *,
        username: str,
        url: str,
        action_status: BookingActionStatus,
        action_time: str | None = None,
    ) -> int:
        """Insert one successful booking action row.

        Caller controls when to store `unbooked`; no automatic rows are inserted.
        """
        if not username.strip():
            raise ValueError("username must be a non-empty string")
        if not url.strip():
            raise ValueError("url must be a non-empty string")
        if action_status not in ("booked", "unbooked"):
            raise ValueError("action_status must be 'booked' or 'unbooked'")

        normalized_action_time = action_time or _now_local_iso()

        async with aiosqlite.connect(self.sqlite_path) as conn:
            cursor = await conn.execute(
                INSERT_BOOKING_HISTORY_SQL,
                (username, url, action_status, normalized_action_time),
            )
            await conn.commit()
            return int(cursor.lastrowid)

    async def get_last_booking_history_for_url(self, url: str) -> BookingHistoryRecord | None:
        """Return latest booking action for URL, or None when no history exists."""
        if not url.strip():
            raise ValueError("url must be a non-empty string")

        async with aiosqlite.connect(self.sqlite_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(SELECT_LAST_BOOKING_HISTORY_FOR_URL_SQL, (url,))
            row = await cursor.fetchone()

        if row is None:
            return None

        return BookingHistoryRecord(**dict(row))

    async def _get_last_result_for_url_conn(
        self,
        conn: aiosqlite.Connection,
        url: str,
    ) -> StoredHealthResult | None:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(SELECT_LAST_FOR_URL_SQL, (url,))
        row = await cursor.fetchone()
        if row is None:
            return None

        data = dict(row)
        return StoredHealthResult(**data)


async def init_db(sqlite_path: str | Path) -> None:
    """Convenience wrapper: initialize schema without direct repository usage."""
    repo = HealthResultRepository(sqlite_path)
    await repo.init_db()


async def save_health_result(sqlite_path: str | Path, result: HealthResult) -> int:
    """Convenience wrapper: persist a `HealthResult` payload."""
    repo = HealthResultRepository(sqlite_path)
    payload = asdict(result)
    payload.pop("id", None)
    return await repo.save_health_result(**payload)


async def get_last_result_for_alias(
    sqlite_path: str | Path,
    alias: str,
) -> StoredHealthResult | None:
    """Convenience wrapper: fetch latest result for an alias."""
    repo = HealthResultRepository(sqlite_path)
    return await repo.get_last_result_for_alias(alias)


async def get_last_result_for_url(
    sqlite_path: str | Path,
    url: str,
) -> StoredHealthResult | None:
    """Convenience wrapper: fetch latest result for an `_hc` URL."""
    repo = HealthResultRepository(sqlite_path)
    return await repo.get_last_result_for_url(url)


async def get_booking_current(
    sqlite_path: str | Path,
    url: str,
) -> BookingCurrentRecord | None:
    """Convenience wrapper: fetch current booking state by URL."""
    repo = HealthResultRepository(sqlite_path)
    return await repo.get_booking_current(url)


async def list_booking_current(
    sqlite_path: str | Path,
) -> list[BookingCurrentRecord]:
    """Convenience wrapper: fetch all current booking rows."""
    repo = HealthResultRepository(sqlite_path)
    return await repo.list_booking_current()


async def upsert_booking_current(
    sqlite_path: str | Path,
    *,
    url: str,
    status: BookingStatus,
    username: str | None = None,
    booked_at: str | None = None,
    booked_until: str | None = None,
) -> None:
    """Convenience wrapper: upsert one current booking row by URL."""
    repo = HealthResultRepository(sqlite_path)
    await repo.upsert_booking_current(
        url=url,
        status=status,
        username=username,
        booked_at=booked_at,
        booked_until=booked_until,
    )


async def set_booking_booked(
    sqlite_path: str | Path,
    *,
    url: str,
    username: str,
    booked_at: str,
    booked_until: str,
) -> None:
    """Convenience wrapper: set current booking state to booked by URL."""
    repo = HealthResultRepository(sqlite_path)
    await repo.set_booking_booked(
        url=url,
        username=username,
        booked_at=booked_at,
        booked_until=booked_until,
    )


async def set_booking_free(
    sqlite_path: str | Path,
    *,
    url: str,
) -> None:
    """Convenience wrapper: set current booking state to free by URL."""
    repo = HealthResultRepository(sqlite_path)
    await repo.set_booking_free(url=url)


async def set_all_bookings_free(
    sqlite_path: str | Path,
) -> int:
    """Convenience wrapper: set all current booking rows to free."""
    repo = HealthResultRepository(sqlite_path)
    return await repo.set_all_bookings_free()


async def insert_booking_history(
    sqlite_path: str | Path,
    *,
    username: str,
    url: str,
    action_status: BookingActionStatus,
    action_time: str | None = None,
) -> int:
    """Convenience wrapper: insert one booking history event."""
    repo = HealthResultRepository(sqlite_path)
    return await repo.insert_booking_history(
        username=username,
        url=url,
        action_status=action_status,
        action_time=action_time,
    )


async def get_last_booking_history_for_url(
    sqlite_path: str | Path,
    url: str,
) -> BookingHistoryRecord | None:
    """Convenience wrapper: fetch latest booking action by URL."""
    repo = HealthResultRepository(sqlite_path)
    return await repo.get_last_booking_history_for_url(url)


def _now_local_iso() -> str:
    """Return local timestamp when response is processed by the bot."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


async def _migrate_commit_column_if_needed(conn: aiosqlite.Connection) -> None:
    """Migrate legacy `commit` column to `commit_hash` if needed."""
    cursor = await conn.execute("PRAGMA table_info(hc_version_results);")
    rows = await cursor.fetchall()
    columns = {row[1] for row in rows if len(row) > 1}

    if "commit" in columns and "commit_hash" not in columns:
        await conn.execute(
            'ALTER TABLE hc_version_results RENAME COLUMN "commit" TO commit_hash;'
        )


def _is_same_meaningful_state(previous: StoredHealthResult, new: HealthResult) -> bool:
    """Return True when fields that define state are equal."""
    return (
        previous.branch == new.branch
        and previous.commit_hash == new.commit_hash
        and previous.tag == new.tag
        and previous.status == new.status
        and previous.error_message == new.error_message
    )
