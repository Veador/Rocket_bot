"""Minimal Rocket.Chat realtime listener (DDP over websocket).

Abstraction layer:
- handles websocket/DDP connect/auth/subscribe details
- emits only parsed `!hc version <alias>` command events to a handler
"""

from __future__ import annotations

import asyncio
from collections import deque
import inspect
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse, urlunparse

import websockets
from websockets.exceptions import ConnectionClosed

from app.config import DEFAULT_CONFIG_PATH, load_config

logger = logging.getLogger(__name__)


class RocketChatRealtimeError(RuntimeError):
    """Base runtime error for realtime listener failures."""


class RocketChatRealtimeAuthError(RocketChatRealtimeError):
    """Raised when DDP login fails."""


class RocketChatRealtimeConfigError(RocketChatRealtimeError):
    """Raised when realtime listener configuration is invalid."""


@dataclass(slots=True)
class IncomingMessage:
    """Minimal incoming message payload extracted from realtime events."""

    message_id: str
    room_id: str
    sender_id: str
    text: str
    thread_message_id: str | None = None


@dataclass(slots=True)
class HcCommandEvent:
    """Command-candidate message event for `!hc ...` commands."""

    message: IncomingMessage


CommandHandler = Callable[[HcCommandEvent], Awaitable[None] | None]


class RocketChatRealtimeListener:
    """Minimal DDP listener focused on command message ingestion."""

    def __init__(
        self,
        *,
        websocket_url: str,
        bot_user_id: str,
        auth_token: str,
        room_ids: list[str],
        ignore_own_messages: bool = True,
        hc_version_command: str = "!hc version",
        reconnect_delay_seconds: float = 3.0,
    ) -> None:
        self.websocket_url = websocket_url.strip()
        self.bot_user_id = bot_user_id.strip()
        self.auth_token = auth_token.strip()
        self.room_ids = [room.strip() for room in room_ids if isinstance(room, str) and room.strip()]
        self.ignore_own_messages = ignore_own_messages
        self.hc_version_command = hc_version_command.strip()
        self.command_prefix = self.hc_version_command.split(" ")[0] if self.hc_version_command else ""
        self.reconnect_delay_seconds = reconnect_delay_seconds

        if not self.websocket_url:
            raise RocketChatRealtimeConfigError("websocket_url must be configured")
        if not self.bot_user_id:
            raise RocketChatRealtimeConfigError("bot_user_id must be configured")
        if not self.auth_token:
            raise RocketChatRealtimeConfigError("auth_token must be configured")
        if not isinstance(self.ignore_own_messages, bool):
            raise RocketChatRealtimeConfigError("ignore_own_messages must be a boolean")
        if not self.hc_version_command:
            raise RocketChatRealtimeConfigError("commands.hc_version must be configured")
        if not self.command_prefix:
            raise RocketChatRealtimeConfigError("commands.hc_version must start with a command prefix")
        if not self.room_ids:
            raise RocketChatRealtimeConfigError(
                "No room IDs configured. Set rocketchat.room_filters for realtime subscription."
            )

        self._message_counter = 0
        self._seen_command_message_ids: set[str] = set()
        self._seen_command_message_order: deque[str] = deque()
        self._max_seen_command_messages = 5000

    @classmethod
    def from_config_file(
        cls,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
    ) -> RocketChatRealtimeListener:
        """Build listener from YAML config (token resolved by config loader)."""
        cfg = load_config(config_path)
        websocket_url = cfg.rocketchat.websocket_url or _derive_websocket_url(cfg.rocketchat.base_url)

        return cls(
            websocket_url=websocket_url,
            bot_user_id=cfg.rocketchat.user_id,
            auth_token=cfg.rocketchat.auth_token,
            room_ids=cfg.rocketchat.room_filters,
            ignore_own_messages=cfg.rocketchat.ignore_own_messages,
            hc_version_command=cfg.commands.hc_version,
        )

    async def run_forever(self, handler: CommandHandler) -> None:
        """Keep listener running and reconnect on disconnect/errors."""
        while True:
            try:
                await self.run_once(handler)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("Realtime listener stopped with error: %s", exc)
                await asyncio.sleep(self.reconnect_delay_seconds)

    async def run_once(self, handler: CommandHandler) -> None:
        """Run a single websocket session until it closes."""
        logger.info("Connecting realtime websocket: %s", self.websocket_url)

        async with websockets.connect(self.websocket_url) as ws:
            await self._ddp_connect(ws)
            await self._ddp_login(ws)
            await self._subscribe_room_streams(ws)

            logger.info("Realtime listener started; subscribed rooms=%s", ", ".join(self.room_ids))

            while True:
                payload = await self._recv_ddp_message(ws)
                event = self._parse_command_event(payload)
                if event is None:
                    continue
                await _invoke_handler(handler, event)

    async def _ddp_connect(self, ws: Any) -> None:
        connect_id = self._next_id()
        await ws.send(
            json.dumps(
                {
                    "msg": "connect",
                    "version": "1",
                    "support": ["1", "pre2", "pre1"],
                    "id": connect_id,
                }
            )
        )

        payload = await self._wait_for(
            ws,
            predicate=lambda msg: msg.get("msg") in {"connected", "failed"},
            wait_context="DDP connect",
        )

        if payload.get("msg") == "failed":
            raise RocketChatRealtimeError(f"DDP connect failed: {payload}")

    async def _ddp_login(self, ws: Any) -> None:
        request_id = self._next_id()
        await ws.send(
            json.dumps(
                {
                    "msg": "method",
                    "method": "login",
                    "id": request_id,
                    "params": [{"resume": self.auth_token}],
                }
            )
        )

        payload = await self._wait_for(
            ws,
            predicate=lambda msg: msg.get("msg") == "result" and msg.get("id") == request_id,
            wait_context="DDP login",
        )

        if payload.get("error"):
            raise RocketChatRealtimeAuthError(f"DDP login failed: {payload.get('error')}")

        logger.info("Realtime DDP login successful")

    async def _subscribe_room_streams(self, ws: Any) -> None:
        for room_id in self.room_ids:
            request_id = self._next_id()
            await ws.send(
                json.dumps(
                    {
                        "msg": "sub",
                        "id": request_id,
                        "name": "stream-room-messages",
                        "params": [room_id, False],
                    }
                )
            )

            payload = await self._wait_for(
                ws,
                predicate=lambda msg, sub_id=request_id: _is_subscribed_response(msg, sub_id),
                wait_context=f"room subscription ({room_id})",
            )

            if payload.get("msg") == "nosub":
                raise RocketChatRealtimeError(
                    f"Subscription rejected for room {room_id}: {payload.get('error')}"
                )

            logger.info("Subscribed to room stream: %s", room_id)

    async def _wait_for(
        self,
        ws: Any,
        *,
        predicate: Callable[[dict[str, Any]], bool],
        wait_context: str,
    ) -> dict[str, Any]:
        while True:
            payload = await self._recv_ddp_message(ws)
            if predicate(payload):
                return payload
            logger.debug("Ignoring DDP message while waiting for %s: %s", wait_context, payload)

    async def _recv_ddp_message(self, ws: Any) -> dict[str, Any]:
        try:
            raw = await ws.recv()
        except ConnectionClosed as exc:
            raise RocketChatRealtimeError(f"Websocket closed: {exc}") from exc

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Ignoring non-JSON websocket payload: %r", raw)
            return {}

        if payload.get("msg") == "ping":
            await ws.send(json.dumps({"msg": "pong"}))
            return {}

        return payload

    def _parse_command_event(self, payload: dict[str, Any]) -> HcCommandEvent | None:
        """Parse realtime changed event and return command-candidate event when matched."""
        if payload.get("msg") != "changed":
            return None
        if payload.get("collection") != "stream-room-messages":
            return None

        fields = payload.get("fields")
        if not isinstance(fields, dict):
            return None

        message = _extract_message(fields)
        if message is None:
            return None

        if self.ignore_own_messages and message.sender_id == self.bot_user_id:
            return None

        # Process only root messages; skip replies inside existing threads.
        if message.thread_message_id is not None:
            return None

        if not _starts_with_command_prefix(message.text, self.command_prefix):
            return None

        if self._is_seen_command_message(message.message_id):
            return None

        self._remember_command_message(message.message_id)
        return HcCommandEvent(message=message)

    def _next_id(self) -> str:
        self._message_counter += 1
        return f"msg-{self._message_counter}"

    def _is_seen_command_message(self, message_id: str) -> bool:
        return message_id in self._seen_command_message_ids

    def _remember_command_message(self, message_id: str) -> None:
        self._seen_command_message_ids.add(message_id)
        self._seen_command_message_order.append(message_id)

        while len(self._seen_command_message_order) > self._max_seen_command_messages:
            oldest = self._seen_command_message_order.popleft()
            self._seen_command_message_ids.discard(oldest)


def _extract_message(fields: dict[str, Any]) -> IncomingMessage | None:
    args = fields.get("args")
    if not isinstance(args, list) or not args:
        return None

    message_payload = next(
        (item for item in args if isinstance(item, dict) and "_id" in item and "msg" in item),
        None,
    )
    if not isinstance(message_payload, dict):
        return None

    message_id = _as_non_empty_string(message_payload.get("_id"))
    room_id = _as_non_empty_string(message_payload.get("rid"))
    text = _as_non_empty_string(message_payload.get("msg"))
    thread_message_id = _as_non_empty_string(message_payload.get("tmid"))

    user_payload = message_payload.get("u")
    sender_id = None
    if isinstance(user_payload, dict):
        sender_id = _as_non_empty_string(user_payload.get("_id"))

    if not message_id or not room_id or not sender_id or not text:
        return None

    return IncomingMessage(
        message_id=message_id,
        room_id=room_id,
        sender_id=sender_id,
        text=text,
        thread_message_id=thread_message_id,
    )


def _starts_with_command_prefix(text: str, command_prefix: str) -> bool:
    text_tokens = text.split()
    return bool(text_tokens) and text_tokens[0] == command_prefix


def _is_subscribed_response(payload: dict[str, Any], sub_id: str) -> bool:
    if payload.get("msg") == "ready":
        subs = payload.get("subs")
        return isinstance(subs, list) and sub_id in subs
    if payload.get("msg") == "nosub" and payload.get("id") == sub_id:
        return True
    return False


def _derive_websocket_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise RocketChatRealtimeConfigError(
            "Cannot derive websocket URL from invalid rocketchat.base_url"
        )

    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/") + "/websocket"
    return urlunparse((ws_scheme, parsed.netloc, path, "", "", ""))


def _as_non_empty_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


async def _invoke_handler(handler: CommandHandler, event: HcCommandEvent) -> None:
    result = handler(event)
    if inspect.isawaitable(result):
        await result
