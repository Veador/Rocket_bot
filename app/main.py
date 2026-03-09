"""Rocket.Chat bot entrypoint.

Startup flow:
1. Load config
2. Initialize SQLite schema
3. Start realtime listener

Command flow:
1. Parse command text using config-driven parser
2. For `!help`: render config template and reply in thread
3. For `!hc version <alias>`: resolve alias -> `_hc` URL
4. Fetch `_hc` version payload and save success/error into SQLite
5. For booking commands: execute booking service and render configured templates
6. Post thread reply under original message
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import logging
import sys

import httpx

from app.booking_service import BookingService, BookingServiceResult
from app.commands import CommandParser, ParsedCommand
from app.config import DEFAULT_CONFIG_PATH, MessagesConfig, load_config
from app.health import fetch_hc_version, format_hc_reply_text
from app.help_text import render_help_message
from app.rc_rest import RocketChatRestClient, RocketChatRestError
from app.rc_realtime import HcCommandEvent, IncomingMessage, RocketChatRealtimeListener
from app.storage import HealthResultRepository
from app.template_renderer import render_template

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
    booking_service = BookingService.from_config(config, repository=repository)

    async with httpx.AsyncClient() as http_client:
        async def handle_command(event: HcCommandEvent) -> None:
            try:
                await _handle_command_message(
                    event=event,
                    parser=parser,
                    help_template=config.help.template,
                    booking_messages=config.messages,
                    repository=repository,
                    booking_service=booking_service,
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
    booking_messages: MessagesConfig,
    repository: HealthResultRepository,
    booking_service: BookingService,
    rest_client: RocketChatRestClient,
    http_client: httpx.AsyncClient,
) -> None:
    """Handle one incoming command message."""
    message = event.message
    logger.info(
        "Received message id=%s room=%s sender=%s username=%s text=%r",
        message.message_id,
        message.room_id,
        message.sender_id,
        message.sender_username,
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
            book_command=parser.book_command_text,
            book_status_command=parser.book_status_command_text,
            unbook_command=parser.unbook_command_text,
            unbook_all_command=parser.unbook_all_command_text,
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

    if parsed.kind in {"book", "book_status", "unbook", "unbook_all"}:
        await _handle_booking_command(
            parsed=parsed,
            message=message,
            booking_service=booking_service,
            booking_messages=booking_messages,
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


async def _handle_booking_command(
    *,
    parsed: ParsedCommand,
    message: IncomingMessage,
    booking_service: BookingService,
    booking_messages: MessagesConfig,
    rest_client: RocketChatRestClient,
) -> None:
    """Execute booking operation and post one thread reply."""
    if parsed.kind == "book":
        result = await booking_service.book(
            alias=parsed.alias,
            username=message.sender_username,
            duration=parsed.duration or "",
        )
    elif parsed.kind == "book_status":
        result = await booking_service.book_status(alias=parsed.alias)
    elif parsed.kind == "unbook_all":
        result = await booking_service.unbook_all(
            username=message.sender_username,
        )
    else:
        result = await booking_service.unbook(
            alias=parsed.alias,
            username=message.sender_username,
        )

    reply_text = _format_booking_reply(
        result=result,
        booking_messages=booking_messages,
        fallback_alias=parsed.alias,
    )
    await _post_thread_reply_safe(
        rest_client=rest_client,
        room_id=message.room_id,
        thread_message_id=message.message_id,
        text=reply_text,
    )


def _format_booking_reply(
    *,
    result: BookingServiceResult,
    booking_messages: MessagesConfig,
    fallback_alias: str,
) -> str:
    """Format booking reply text using config templates only."""
    if result.action == "status" and result.outcome == "booked":
        template = booking_messages.booking_busy
    elif result.outcome == "booked":
        template = booking_messages.booking_success
    elif result.outcome == "busy":
        template = booking_messages.booking_busy
    elif result.outcome == "free":
        template = booking_messages.booking_free
    elif result.outcome == "unbooked":
        template = booking_messages.unbooking_success
    elif result.outcome == "unbooked_all":
        template = booking_messages.unbooking_all_success
    elif result.outcome == "incorrect_or_missing_time":
        template = booking_messages.incorrect_or_missing_time
    else:
        template = booking_messages.incorrect_alias

    env_name = result.env_name or fallback_alias or "-"
    duration_text = (result.remaining_time or "").strip()
    return render_template(
        template,
        {
            "env_name": env_name,
            "time": duration_text,
            "username": result.username or "",
            "remaining_time": duration_text,
            "count": str(result.affected_count or 0),
        },
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
