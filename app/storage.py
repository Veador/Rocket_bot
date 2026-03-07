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

from app.models import HealthResult, HealthStatus, StoredHealthResult

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
