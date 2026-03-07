"""Minimal Rocket.Chat REST client for posting bot replies.

Focused project scope:
- post a thread reply under the original command message
- provide a tiny normal message helper for future use
"""

from __future__ import annotations

from pathlib import Path
import logging

import httpx

from app.config import DEFAULT_CONFIG_PATH, RocketChatConfig, load_config

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=10.0)


class RocketChatRestError(RuntimeError):
    """Raised when a Rocket.Chat REST call fails."""


class RocketChatRestClient:
    """Small REST client for `chat.postMessage`."""

    def __init__(
        self,
        *,
        base_url: str,
        user_id: str,
        auth_token: str,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if not user_id.strip():
            raise ValueError("user_id must be a non-empty string")
        if not auth_token.strip():
            raise ValueError("auth_token must be a non-empty string")

        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.auth_token = auth_token
        self.timeout = timeout

    @classmethod
    def from_rocketchat_config(cls, config: RocketChatConfig) -> RocketChatRestClient:
        """Build client from already-loaded bot config."""
        return cls(
            base_url=config.base_url,
            user_id=config.user_id,
            auth_token=config.auth_token,
        )

    @classmethod
    def from_config_file(
        cls,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
    ) -> RocketChatRestClient:
        """Build client from YAML config (token comes from env via config loader)."""
        bot_config = load_config(config_path)
        return cls.from_rocketchat_config(bot_config.rocketchat)

    async def post_thread_reply(self, room_id: str, thread_message_id: str, text: str) -> None:
        """Post a message in thread under the original command message."""
        await self.post_message(room_id=room_id, text=text, thread_message_id=thread_message_id)

    async def post_message(
        self,
        *,
        room_id: str,
        text: str,
        thread_message_id: str | None = None,
    ) -> None:
        """Post a normal message, optionally as a thread reply (`tmid`)."""
        if not room_id.strip():
            raise ValueError("room_id must be a non-empty string")
        if not text.strip():
            raise ValueError("text must be a non-empty string")

        payload: dict[str, str] = {
            "roomId": room_id,
            "text": text,
        }
        if thread_message_id is not None:
            if not thread_message_id.strip():
                raise ValueError("thread_message_id must be a non-empty string when provided")
            payload["tmid"] = thread_message_id

        await self._post_json("/api/v1/chat.postMessage", payload)

    async def _post_json(self, api_path: str, payload: dict[str, str]) -> None:
        """Send authenticated JSON POST to Rocket.Chat API and validate response."""
        url = f"{self.base_url}{api_path}"

        headers = {
            "X-User-Id": self.user_id,
            "X-Auth-Token": self.auth_token,
            "Content-Type": "application/json",
        }

        logger.info("POST %s", url)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            logger.error("Rocket.Chat request timed out for %s", url)
            raise RocketChatRestError("Rocket.Chat request timed out") from exc
        except httpx.RequestError as exc:
            logger.error("Rocket.Chat request error for %s: %s", url, exc)
            raise RocketChatRestError(f"Rocket.Chat request error: {exc}") from exc

        if response.status_code != 200:
            body = _compact_text(response.text)
            logger.error("Rocket.Chat API HTTP %s for %s: %s", response.status_code, url, body)
            raise RocketChatRestError(
                f"Rocket.Chat API returned HTTP {response.status_code}: {body}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            logger.error("Rocket.Chat API returned invalid JSON for %s", url)
            raise RocketChatRestError("Rocket.Chat API returned invalid JSON") from exc

        if data.get("success") is not True:
            api_error = data.get("error") or data.get("message") or "unknown API error"
            logger.error("Rocket.Chat API error for %s: %s", url, api_error)
            raise RocketChatRestError(f"Rocket.Chat API error: {api_error}")

        logger.info("Rocket.Chat message posted successfully to room %s", payload.get("roomId"))


async def post_thread_reply(
    room_id: str,
    thread_message_id: str,
    text: str,
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
) -> None:
    """Convenience helper: load config and post thread reply."""
    client = RocketChatRestClient.from_config_file(config_path)
    await client.post_thread_reply(room_id=room_id, thread_message_id=thread_message_id, text=text)


async def post_message(
    room_id: str,
    text: str,
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
) -> None:
    """Tiny helper for future use: post normal (non-thread) message."""
    client = RocketChatRestClient.from_config_file(config_path)
    await client.post_message(room_id=room_id, text=text)


def _compact_text(raw: str, max_len: int = 300) -> str:
    """Return one-line compact text for readable errors/logs."""
    compact = " ".join(raw.split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3] + "..."
