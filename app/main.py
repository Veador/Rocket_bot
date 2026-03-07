"""Rocket.Chat bot PoC entrypoint.

Startup flow:
1. Load config
2. Initialize SQLite schema
3. Start realtime listener

Command flow:
1. Parse `!hc ...` command text using config-driven parser
2. For `!hc help`: render config template and reply in thread
3. For `!hc version <alias>`: resolve alias -> `_hc` URL
4. Fetch `_hc` version payload and save success/error into SQLite
5. Post thread reply under original message
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import logging
import sys

import httpx

from app.commands import CommandParser, ParsedCommand
from app.config import DEFAULT_CONFIG_PATH, load_config
from app.health import fetch_hc_version, format_hc_reply_text
from app.help_text import render_help_message
from app.rc_rest import RocketChatRestClient, RocketChatRestError
from app.rc_realtime import HcCommandEvent, IncomingMessage, RocketChatRealtimeListener
from app.storage import HealthResultRepository

logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    """Configure root logging once at startup."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


async def run(config_path: str | Path = DEFAULT_CONFIG_PATH) -> None:
    """Run the bot forever."""
    config = load_config(config_path)
    configure_logging(config.logging.level)

    logger.info("Configuration loaded from %s", config_path)
    logger.info("Initializing SQLite database: %s", config.database.sqlite_path)

    repository = HealthResultRepository(config.database.sqlite_path)
    await repository.init_db()

    parser = CommandParser.from_config(config)
    rest_client = RocketChatRestClient.from_rocketchat_config(config.rocketchat)
    listener = RocketChatRealtimeListener.from_config_file(config_path)

    async with httpx.AsyncClient() as http_client:
        async def handle_command(event: HcCommandEvent) -> None:
            try:
                await _handle_command_message(
                    event=event,
                    parser=parser,
                    help_template=config.help.template,
                    repository=repository,
                    rest_client=rest_client,
                    http_client=http_client,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unhandled error while processing command event: %s", exc)

        logger.info("Starting Rocket.Chat realtime listener")
        await listener.run_forever(handle_command)


async def _handle_command_message(
    *,
    event: HcCommandEvent,
    parser: CommandParser,
    help_template: str,
    repository: HealthResultRepository,
    rest_client: RocketChatRestClient,
    http_client: httpx.AsyncClient,
) -> None:
    """Handle one incoming `!hc ...` command message."""
    message = event.message
    logger.info(
        "Received message id=%s room=%s sender=%s text=%r",
        message.message_id,
        message.room_id,
        message.sender_id,
        message.text,
    )

    parsed = parser.parse_message(message.text)
    if parsed is None:
        logger.debug("Ignoring unsupported or invalid command text: %r", message.text)
        return

    if parsed.kind == "help":
        reply_text = render_help_message(
            template=help_template,
            version_command=parser.command_text,
            help_command=parser.help_command_text,
            environments=parser.environments,
        )
        await _post_thread_reply_safe(
            rest_client=rest_client,
            room_id=message.room_id,
            thread_message_id=message.message_id,
            text=reply_text,
        )
        return

    if parsed.is_error:
        await _handle_command_error(
            parsed=parsed,
            message=message,
            repository=repository,
            rest_client=rest_client,
        )
        return

    assert parsed.url is not None

    logger.info("Fetching _hc alias=%s url=%s", parsed.alias, parsed.url)
    fetch_result = await fetch_hc_version(
        url=parsed.url,
        alias=parsed.alias,
        environment_name=parsed.environment_name,
        client=http_client,
    )

    await repository.save_health_result(
        alias=fetch_result.alias,
        url=fetch_result.url,
        branch=fetch_result.branch,
        commit_hash=fetch_result.commit_hash,
        tag=fetch_result.tag,
        hc_timestamp=fetch_result.hc_timestamp,
        status=fetch_result.status,
        error_message=fetch_result.error_message,
    )
    logger.info("Saved health result alias=%s status=%s", fetch_result.alias, fetch_result.status)

    reply_text = format_hc_reply_text(fetch_result)
    await _post_thread_reply_safe(
        rest_client=rest_client,
        room_id=message.room_id,
        thread_message_id=message.message_id,
        text=reply_text,
    )


def _format_alias_error_reply(parsed: ParsedCommand) -> str:
    """Build chat-safe reply for alias resolution errors."""
    return parsed.error_message or "Invalid command"


async def _handle_command_error(
    *,
    parsed: ParsedCommand,
    message: IncomingMessage,
    repository: HealthResultRepository,
    rest_client: RocketChatRestClient,
) -> None:
    """Persist and reply for command-level errors (e.g., unknown alias)."""
    error_message = parsed.error_message or "Invalid command"

    logger.warning("Command error alias=%s error=%s", parsed.alias, error_message)

    await repository.save_health_result(
        alias=parsed.alias,
        url=parsed.url or "<unresolved>",
        branch=None,
        commit_hash=None,
        tag=None,
        hc_timestamp=None,
        status="error",
        error_message=error_message,
    )
    logger.info("Saved command error for alias=%s", parsed.alias)

    await _post_thread_reply_safe(
        rest_client=rest_client,
        room_id=message.room_id,
        thread_message_id=message.message_id,
        text=_format_alias_error_reply(parsed),
    )


async def _post_thread_reply_safe(
    *,
    rest_client: RocketChatRestClient,
    room_id: str,
    thread_message_id: str,
    text: str,
) -> None:
    """Post thread reply and log errors without crashing listener loop."""
    try:
        await rest_client.post_thread_reply(
            room_id=room_id,
            thread_message_id=thread_message_id,
            text=text,
        )
        logger.info("Posted thread reply room=%s thread=%s", room_id, thread_message_id)
    except RocketChatRestError as exc:
        logger.error("Failed to post thread reply room=%s thread=%s: %s", room_id, thread_message_id, exc)


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG_PATH
    try:
        asyncio.run(run(path))
    except KeyboardInterrupt:
        logger.info("Shutting down bot")
