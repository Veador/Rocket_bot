"""Health endpoint client for `!hc version <alias>`.

This module fetches an `_hc` endpoint and parses only:
- version.branch
- version.commit
- version.tag
- top-level timestamp
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx

HealthFetchStatus = Literal["success", "error"]

DEFAULT_TIMEOUT = httpx.Timeout(connect=3.0, read=8.0, write=8.0, pool=8.0)


@dataclass(slots=True)
class HealthFetchResult:
    """Structured result of one `_hc` fetch attempt."""

    alias: str
    url: str
    environment_name: str | None = None
    branch: str | None = None
    commit_hash: str | None = None
    tag: str | None = None
    hc_timestamp: str | None = None
    status: HealthFetchStatus = "success"
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success"


async def fetch_hc_version(
    url: str,
    alias: str,
    environment_name: str | None = None,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
) -> HealthFetchResult:
    """Fetch and parse `_hc` payload for one alias.

    Args:
        url: Full `_hc` URL to request.
        alias: Environment alias used in chat and storage.
        client: Optional shared `httpx.AsyncClient`.
        timeout: Explicit request timeouts.
    """
    if not alias.strip():
        return _error_result(
            alias=alias,
            url=url,
            environment_name=environment_name,
            message="Invalid alias: empty value",
        )

    if not url.strip():
        return _error_result(
            alias=alias,
            url=url,
            environment_name=environment_name,
            message="Invalid _hc URL: empty value",
        )

    managed_client = client is None

    try:
        if managed_client:
            async with httpx.AsyncClient(timeout=timeout) as local_client:
                response = await local_client.get(url)
        else:
            response = await client.get(url, timeout=timeout)
    except httpx.TimeoutException:
        return _error_result(
            alias=alias,
            url=url,
            environment_name=environment_name,
            message="Request timed out while calling _hc endpoint",
        )
    except httpx.RequestError as exc:
        return _error_result(
            alias=alias,
            url=url,
            environment_name=environment_name,
            message=f"HTTP request error: {exc}",
        )

    if response.status_code != 200:
        return _error_result(
            alias=alias,
            url=url,
            environment_name=environment_name,
            message=f"_hc endpoint returned HTTP {response.status_code}",
        )

    try:
        payload = response.json()
    except ValueError:
        return _error_result(
            alias=alias,
            url=url,
            environment_name=environment_name,
            message="Invalid JSON in _hc response",
        )

    return _parse_hc_payload(
        alias=alias,
        url=url,
        environment_name=environment_name,
        payload=payload,
    )


def format_hc_reply_text(result: HealthFetchResult) -> str:
    """Build a short human-readable Rocket.Chat reply text."""
    environment_label = result.environment_name or result.alias
    healthcheck_link = f"[Healthcheck link]({result.url})"

    if not result.ok:
        message = result.error_message or "Unknown error"
        return "\n".join(
            [
                f"Environment: {environment_label}",
                f"Error: {message}",
                healthcheck_link,
            ]
        )

    lines = [
        f"Environment: {environment_label}",
        f"Branch: {result.branch}",
        f"Commit: {result.commit_hash}",
    ]

    if isinstance(result.tag, str) and result.tag.strip():
        lines.append(f"Tag: {result.tag.strip()}")

    lines.append(healthcheck_link)
    return "\n".join(lines)


def _parse_hc_payload(
    alias: str,
    url: str,
    environment_name: str | None,
    payload: Any,
) -> HealthFetchResult:
    """Parse only required fields from `_hc` payload."""
    if not isinstance(payload, dict):
        return _error_result(
            alias=alias,
            url=url,
            environment_name=environment_name,
            message="Invalid _hc JSON shape: expected object",
        )

    timestamp = payload.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp.strip():
        return _error_result(
            alias=alias,
            url=url,
            environment_name=environment_name,
            message="Missing required field: timestamp",
        )

    version = payload.get("version")
    if not isinstance(version, dict):
        return _error_result(
            alias=alias,
            url=url,
            environment_name=environment_name,
            message="Missing required object: version",
        )

    branch = version.get("branch")
    if not isinstance(branch, str) or not branch.strip():
        return _error_result(
            alias=alias,
            url=url,
            environment_name=environment_name,
            message="Missing required field: version.branch",
        )

    commit = version.get("commit")
    if not isinstance(commit, str) or not commit.strip():
        return _error_result(
            alias=alias,
            url=url,
            environment_name=environment_name,
            message="Missing required field: version.commit",
        )

    raw_tag = version.get("tag")
    if raw_tag is None:
        tag: str | None = None
    elif isinstance(raw_tag, str):
        tag = raw_tag.strip() or None
    else:
        return _error_result(
            alias=alias,
            url=url,
            environment_name=environment_name,
            message="Invalid field type: version.tag",
        )

    return HealthFetchResult(
        alias=alias,
        url=url,
        environment_name=environment_name or alias,
        branch=branch.strip(),
        commit_hash=commit.strip(),
        tag=tag,
        hc_timestamp=timestamp.strip(),
        status="success",
        error_message=None,
    )


def _error_result(
    alias: str,
    url: str,
    environment_name: str | None,
    message: str,
) -> HealthFetchResult:
    """Create a compact error result for storage and chat reply."""
    alias_value = alias if alias.strip() else "<unknown>"
    return HealthFetchResult(
        alias=alias_value,
        url=url,
        environment_name=environment_name or alias_value,
        status="error",
        error_message=message,
    )
