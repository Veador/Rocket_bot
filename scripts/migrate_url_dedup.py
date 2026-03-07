#!/usr/bin/env python3
"""Deduplicate legacy `hc_version_results` rows by URL + meaningful state.

This migration collapses duplicate rows that represent the same state:
    (url, branch, commit_hash, tag, status, error_message)

For each group, it keeps one row and retains:
- alias from the newest row by fetched_at (tie-breaker: id DESC)
- newest hc_timestamp in the group
- newest fetched_at in the group

It recreates the table in a transaction, then restores indexes used by app code.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


MIGRATION_SQL = """
PRAGMA foreign_keys=OFF;
BEGIN IMMEDIATE;

CREATE TABLE hc_version_results_new (
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

WITH normalized AS (
    SELECT
        id,
        alias,
        url,
        branch,
        commit_hash,
        tag,
        status,
        error_message,
        hc_timestamp,
        fetched_at,
        COALESCE(julianday(fetched_at), -1.0e18) AS fetched_jd,
        COALESCE(julianday(hc_timestamp), -1.0e18) AS hc_jd
    FROM hc_version_results
),
grouped AS (
    SELECT
        n.url,
        n.branch,
        n.commit_hash,
        n.tag,
        n.status,
        n.error_message,
        (
            SELECT n2.id
            FROM normalized n2
            WHERE n2.url IS n.url
              AND n2.branch IS n.branch
              AND n2.commit_hash IS n.commit_hash
              AND n2.tag IS n.tag
              AND n2.status IS n.status
              AND n2.error_message IS n.error_message
            ORDER BY n2.fetched_jd DESC, n2.fetched_at DESC, n2.id DESC
            LIMIT 1
        ) AS source_id,
        (
            SELECT n3.hc_timestamp
            FROM normalized n3
            WHERE n3.url IS n.url
              AND n3.branch IS n.branch
              AND n3.commit_hash IS n.commit_hash
              AND n3.tag IS n.tag
              AND n3.status IS n.status
              AND n3.error_message IS n.error_message
            ORDER BY n3.hc_jd DESC, n3.hc_timestamp DESC, n3.id DESC
            LIMIT 1
        ) AS newest_hc_timestamp,
        (
            SELECT n4.fetched_at
            FROM normalized n4
            WHERE n4.url IS n.url
              AND n4.branch IS n.branch
              AND n4.commit_hash IS n.commit_hash
              AND n4.tag IS n.tag
              AND n4.status IS n.status
              AND n4.error_message IS n.error_message
            ORDER BY n4.fetched_jd DESC, n4.fetched_at DESC, n4.id DESC
            LIMIT 1
        ) AS newest_fetched_at
    FROM normalized n
    GROUP BY
        n.url,
        n.branch,
        n.commit_hash,
        n.tag,
        n.status,
        n.error_message
)
INSERT INTO hc_version_results_new (
    alias,
    url,
    branch,
    commit_hash,
    tag,
    hc_timestamp,
    fetched_at,
    status,
    error_message
)
SELECT
    src.alias,
    src.url,
    src.branch,
    src.commit_hash,
    src.tag,
    g.newest_hc_timestamp,
    g.newest_fetched_at,
    src.status,
    src.error_message
FROM grouped g
JOIN normalized src ON src.id = g.source_id
ORDER BY COALESCE(julianday(g.newest_fetched_at), -1.0e18), g.source_id;

DROP TABLE hc_version_results;
ALTER TABLE hc_version_results_new RENAME TO hc_version_results;

CREATE INDEX IF NOT EXISTS idx_hc_version_results_alias_fetched_at
ON hc_version_results(alias, fetched_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_hc_version_results_url_fetched_at
ON hc_version_results(url, fetched_at DESC, id DESC);
COMMIT;
"""


def migrate(sqlite_path: Path) -> tuple[int, int]:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"Database file not found: {sqlite_path}")

    conn = sqlite3.connect(sqlite_path)
    try:
        before = int(conn.execute("SELECT COUNT(*) FROM hc_version_results;").fetchone()[0])
        conn.executescript(MIGRATION_SQL)
        after = int(conn.execute("SELECT COUNT(*) FROM hc_version_results;").fetchone()[0])
        return before, after
    except Exception:
        try:
            conn.execute("ROLLBACK;")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deduplicate hc_version_results by URL + meaningful state."
    )
    parser.add_argument(
        "sqlite_path",
        nargs="?",
        default="data/bot.db",
        help="Path to SQLite DB file (default: data/bot.db)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.sqlite_path)
    before, after = migrate(db_path)
    print(f"Migration complete: {db_path}")
    print(f"Rows before: {before}")
    print(f"Rows after : {after}")
    print(f"Collapsed  : {before - after}")


if __name__ == "__main__":
    main()
