"""Configuration loader with fail-fast validation.

Example:
    from app.config import load_config

    cfg = load_config()
    print(cfg.rocketchat.base_url)
    print(cfg.commands.hc_version)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import os

import yaml

DEFAULT_CONFIG_PATH = Path("config/bot_config.yaml")
DEFAULT_HELP_TEMPLATE = """Available commands:
{{commands}}

Available aliases:
{{aliases}}"""
DEFAULT_HELP_COMMAND = "!help"
DEFAULT_BOOK_COMMAND = "!book"
DEFAULT_BOOK_STATUS_COMMAND = "!book status"
DEFAULT_UNBOOK_COMMAND = "!unbook"

DEFAULT_BOOKING_SUCCESS_TEMPLATE = (
    "Environment {{env_name}} is booked by {{username}} for another {{remaining_time}}."
)
DEFAULT_BOOKING_BUSY_TEMPLATE = (
    "Environment {{env_name}} is busy. Remaining time: {{remaining_time}}."
)
DEFAULT_BOOKING_FREE_TEMPLATE = "Environment {{env_name}} is free."
DEFAULT_UNBOOKING_SUCCESS_TEMPLATE = "Environment {{env_name}} was unbooked by {{username}}."
DEFAULT_UNBOOKING_ALL_SUCCESS_TEMPLATE = "All environments are successfully unbooked."
DEFAULT_INCORRECT_ALIAS_TEMPLATE = "Incorrect alias: {{env_name}}."
DEFAULT_INCORRECT_OR_MISSING_TIME_TEMPLATE = "Incorrect or missing time."


@dataclass(slots=True)
class RocketChatConfig:
    """Rocket.Chat connection settings."""

    base_url: str
    user_id: str
    auth_token_env: str
    auth_token: str = field(repr=False)
    ignore_own_messages: bool = True
    websocket_url: str | None = None
    room_filters: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _validate_url(self.base_url, field_name="rocketchat.base_url", schemes={"http", "https"})

        if not self.user_id.strip():
            raise ValueError("rocketchat.user_id must be a non-empty string")

        if not self.auth_token_env.strip():
            raise ValueError("rocketchat.auth_token_env must be a non-empty string")

        if not self.auth_token.strip():
            raise ValueError(
                f"Environment variable {self.auth_token_env!r} is empty; set a valid auth token"
            )

        if not isinstance(self.ignore_own_messages, bool):
            raise ValueError("rocketchat.ignore_own_messages must be a boolean")

        if self.websocket_url:
            _validate_url(
                self.websocket_url,
                field_name="rocketchat.websocket_url",
                schemes={"ws", "wss"},
            )

        if not isinstance(self.room_filters, list):
            raise ValueError("rocketchat.room_filters must be a list of room IDs")

        if any((not isinstance(room, str) or not room.strip()) for room in self.room_filters):
            raise ValueError("rocketchat.room_filters must contain only non-empty strings")


@dataclass(slots=True)
class CommandsConfig:
    """Configurable command names."""

    hc_version: str
    hc_help: str
    book: str
    book_status: str
    unbook: str
    unbook_all: str

    def __post_init__(self) -> None:
        if not self.hc_version.strip():
            raise ValueError("commands.hc_version must be a non-empty string")
        if not self.hc_version.startswith("!"):
            raise ValueError("commands.hc_version must start with '!' (for example '!hc version')")
        if not self.hc_help.strip():
            raise ValueError("commands.hc_help must be a non-empty string")
        if not self.hc_help.startswith("!"):
            raise ValueError("commands.hc_help must start with '!' (for example '!help')")

        if not self.book.strip():
            raise ValueError("commands.book must be a non-empty string")
        if not self.book.startswith("!"):
            raise ValueError("commands.book must start with '!' (for example '!book')")

        if not self.book_status.strip():
            raise ValueError("commands.book_status must be a non-empty string")
        if not self.book_status.startswith("!"):
            raise ValueError("commands.book_status must start with '!' (for example '!book status')")

        if not self.unbook.strip():
            raise ValueError("commands.unbook must be a non-empty string")
        if not self.unbook.startswith("!"):
            raise ValueError("commands.unbook must start with '!' (for example '!unbook')")
        if not self.unbook_all.strip():
            raise ValueError("commands.unbook_all must be a non-empty string")
        if not self.unbook_all.startswith("!"):
            raise ValueError("commands.unbook_all must start with '!' (for example '!unbook all')")

        book_prefix = self.book.split(" ")[0]
        book_status_prefix = self.book_status.split(" ")[0]
        if book_prefix != book_status_prefix:
            raise ValueError("commands.book_status must use the same command prefix as commands.book")

        unbook_prefix = self.unbook.split(" ")[0]
        unbook_all_prefix = self.unbook_all.split(" ")[0]
        if unbook_prefix != unbook_all_prefix:
            raise ValueError("commands.unbook_all must use the same command prefix as commands.unbook")


@dataclass(slots=True)
class EnvironmentConfig:
    """Environment alias mapping target."""

    url: str
    name: str | None = None


@dataclass(slots=True)
class HelpConfig:
    """Help command rendering settings."""

    template: str = DEFAULT_HELP_TEMPLATE

    def __post_init__(self) -> None:
        if not isinstance(self.template, str) or not self.template.strip():
            raise ValueError("help.template must be a non-empty string")


@dataclass(slots=True)
class MessagesConfig:
    """User-facing message templates for booking-related replies."""

    booking_success: str = DEFAULT_BOOKING_SUCCESS_TEMPLATE
    booking_busy: str = DEFAULT_BOOKING_BUSY_TEMPLATE
    booking_free: str = DEFAULT_BOOKING_FREE_TEMPLATE
    unbooking_success: str = DEFAULT_UNBOOKING_SUCCESS_TEMPLATE
    unbooking_all_success: str = DEFAULT_UNBOOKING_ALL_SUCCESS_TEMPLATE
    incorrect_alias: str = DEFAULT_INCORRECT_ALIAS_TEMPLATE
    incorrect_or_missing_time: str = DEFAULT_INCORRECT_OR_MISSING_TIME_TEMPLATE

    def __post_init__(self) -> None:
        if not self.booking_success.strip():
            raise ValueError("messages.booking_success must be a non-empty string")
        if not self.booking_busy.strip():
            raise ValueError("messages.booking_busy must be a non-empty string")
        if not self.booking_free.strip():
            raise ValueError("messages.booking_free must be a non-empty string")
        if not self.unbooking_success.strip():
            raise ValueError("messages.unbooking_success must be a non-empty string")
        if not self.unbooking_all_success.strip():
            raise ValueError("messages.unbooking_all_success must be a non-empty string")
        if not self.incorrect_alias.strip():
            raise ValueError("messages.incorrect_alias must be a non-empty string")
        if not self.incorrect_or_missing_time.strip():
            raise ValueError("messages.incorrect_or_missing_time must be a non-empty string")


@dataclass(slots=True)
class DatabaseConfig:
    """Storage settings."""

    sqlite_path: str

    def __post_init__(self) -> None:
        if not self.sqlite_path.strip():
            raise ValueError("database.sqlite_path must be a non-empty string")


@dataclass(slots=True)
class LoggingConfig:
    """Logging settings."""

    level: str = "INFO"

    def __post_init__(self) -> None:
        allowed_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        self.level = self.level.upper()
        if self.level not in allowed_levels:
            raise ValueError(
                "logging.level must be one of: CRITICAL, ERROR, WARNING, INFO, DEBUG"
            )


@dataclass(slots=True)
class BotConfig:
    """Full bot configuration object."""

    rocketchat: RocketChatConfig
    commands: CommandsConfig
    environments: dict[str, EnvironmentConfig]
    help: HelpConfig
    messages: MessagesConfig
    database: DatabaseConfig
    logging: LoggingConfig

    def __post_init__(self) -> None:
        if not self.environments:
            raise ValueError("environments must define at least one alias -> _hc URL mapping")

        for alias, env in self.environments.items():
            if not alias.strip():
                raise ValueError("environments contains an empty alias key")

            _validate_url(
                env.url,
                field_name=f"environments.{alias}",
                schemes={"http", "https"},
            )

            path = urlparse(env.url).path.rstrip("/")
            if not path.endswith("/_hc"):
                raise ValueError(f"environments.{alias} must point to an _hc endpoint")

            if env.name is not None and not env.name.strip():
                raise ValueError(f"environments.{alias}.name must be a non-empty string when set")


def load_config(config_path: str | Path = DEFAULT_CONFIG_PATH) -> BotConfig:
    """Load, validate, and return bot config from YAML.

    Raises:
        FileNotFoundError: if config file is missing.
        ValueError: for invalid YAML or failed validation.
    """

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as file:
            raw = yaml.safe_load(file) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("Config root must be a YAML mapping")

    rocketchat_raw = _require_mapping(raw, "rocketchat")
    commands_raw = _require_mapping(raw, "commands")
    environments_raw = _require_mapping(raw, "environments")
    database_raw = _require_mapping(raw, "database")

    logging_raw_any = raw.get("logging", {"level": "INFO"})
    if not isinstance(logging_raw_any, dict):
        raise ValueError("logging must be a mapping")

    help_raw_any = raw.get("help", {"template": DEFAULT_HELP_TEMPLATE})
    if not isinstance(help_raw_any, dict):
        raise ValueError("help must be a mapping")

    messages_raw_any = raw.get("messages", {})
    if not isinstance(messages_raw_any, dict):
        raise ValueError("messages must be a mapping")

    auth_token_env = _require_string(rocketchat_raw, "auth_token_env", "rocketchat")
    auth_token = os.getenv(auth_token_env, "")
    if not auth_token:
        raise ValueError(
            f"Environment variable {auth_token_env!r} is not set or empty "
            "(required by rocketchat.auth_token_env)"
        )

    rocketchat = RocketChatConfig(
        base_url=_require_string(rocketchat_raw, "base_url", "rocketchat"),
        user_id=_require_string(rocketchat_raw, "user_id", "rocketchat"),
        auth_token_env=auth_token_env,
        auth_token=auth_token,
        ignore_own_messages=_optional_bool(
            rocketchat_raw,
            "ignore_own_messages",
            "rocketchat",
            default=True,
        ),
        websocket_url=_optional_string(rocketchat_raw, "websocket_url", "rocketchat"),
        room_filters=_optional_string_list(rocketchat_raw, "room_filters", "rocketchat"),
    )

    hc_version_command = _require_string(commands_raw, "hc_version", "commands")
    hc_help_command = _optional_string(commands_raw, "hc_help", "commands")
    if hc_help_command is None:
        hc_help_command = DEFAULT_HELP_COMMAND
    book_command = _optional_string(commands_raw, "book", "commands") or DEFAULT_BOOK_COMMAND
    book_status_command = (
        _optional_string(commands_raw, "book_status", "commands")
        or f"{book_command.split(' ')[0]} status"
    )
    unbook_command = _optional_string(commands_raw, "unbook", "commands") or DEFAULT_UNBOOK_COMMAND
    unbook_all_command = (
        _optional_string(commands_raw, "unbook_all", "commands")
        or f"{unbook_command} all"
    )

    commands = CommandsConfig(
        hc_version=hc_version_command,
        hc_help=hc_help_command,
        book=book_command,
        book_status=book_status_command,
        unbook=unbook_command,
        unbook_all=unbook_all_command,
    )

    environments = _environment_mapping(environments_raw, section_name="environments")

    help_config = HelpConfig(
        template=_optional_string(help_raw_any, "template", "help") or DEFAULT_HELP_TEMPLATE,
    )

    messages_config = MessagesConfig(
        booking_success=(
            _optional_string(messages_raw_any, "booking_success", "messages")
            or DEFAULT_BOOKING_SUCCESS_TEMPLATE
        ),
        booking_busy=(
            _optional_string(messages_raw_any, "booking_busy", "messages")
            or DEFAULT_BOOKING_BUSY_TEMPLATE
        ),
        booking_free=(
            _optional_string(messages_raw_any, "booking_free", "messages")
            or DEFAULT_BOOKING_FREE_TEMPLATE
        ),
        unbooking_success=(
            _optional_string(messages_raw_any, "unbooking_success", "messages")
            or DEFAULT_UNBOOKING_SUCCESS_TEMPLATE
        ),
        unbooking_all_success=(
            _optional_string(messages_raw_any, "unbooking_all_success", "messages")
            or DEFAULT_UNBOOKING_ALL_SUCCESS_TEMPLATE
        ),
        incorrect_alias=(
            _optional_string(messages_raw_any, "incorrect_alias", "messages")
            or DEFAULT_INCORRECT_ALIAS_TEMPLATE
        ),
        incorrect_or_missing_time=(
            _optional_string(messages_raw_any, "incorrect_or_missing_time", "messages")
            or DEFAULT_INCORRECT_OR_MISSING_TIME_TEMPLATE
        ),
    )

    database = DatabaseConfig(
        sqlite_path=_require_string(database_raw, "sqlite_path", "database"),
    )

    logging = LoggingConfig(
        level=_optional_string(logging_raw_any, "level", "logging") or "INFO",
    )

    return BotConfig(
        rocketchat=rocketchat,
        commands=commands,
        environments=environments,
        help=help_config,
        messages=messages_config,
        database=database,
        logging=logging,
    )


def _require_mapping(root: dict[str, Any], key: str) -> dict[str, Any]:
    value = root.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} section is required and must be a mapping")
    return value


def _require_string(section: dict[str, Any], key: str, section_name: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{section_name}.{key} must be a non-empty string")
    return value


def _optional_string(section: dict[str, Any], key: str, section_name: str) -> str | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{section_name}.{key} must be a string")
    return value


def _optional_bool(
    section: dict[str, Any],
    key: str,
    section_name: str,
    *,
    default: bool,
) -> bool:
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{section_name}.{key} must be a boolean")
    return value


def _optional_string_list(section: dict[str, Any], key: str, section_name: str) -> list[str]:
    value = section.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{section_name}.{key} must be a list of strings")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{section_name}.{key} must contain only strings")
    return value


def _environment_mapping(section: dict[str, Any], section_name: str) -> dict[str, EnvironmentConfig]:
    result: dict[str, EnvironmentConfig] = {}
    for key, value in section.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{section_name} keys must be non-empty strings")

        if isinstance(value, str):
            url = value.strip()
            if not url:
                raise ValueError(f"{section_name}.{key} must be a non-empty string")
            result[key] = EnvironmentConfig(url=url, name=None)
            continue

        if not isinstance(value, dict):
            raise ValueError(
                f"{section_name}.{key} must be either a URL string or a mapping with fields: url, name"
            )

        raw_url = value.get("url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            raise ValueError(f"{section_name}.{key}.url must be a non-empty string")

        raw_name = value.get("name")
        if raw_name is None:
            name: str | None = None
        elif not isinstance(raw_name, str):
            raise ValueError(f"{section_name}.{key}.name must be a string")
        else:
            name = raw_name.strip() or None

        result[key] = EnvironmentConfig(
            url=raw_url.strip(),
            name=name,
        )
    return result


def _validate_url(url: str, field_name: str, schemes: set[str]) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in schemes or not parsed.netloc:
        allowed = ", ".join(sorted(schemes))
        raise ValueError(f"{field_name} must be a valid URL with scheme one of: {allowed}")


if __name__ == "__main__":
    config = load_config()
    print("Loaded config successfully")
    print(f"Rocket.Chat: {config.rocketchat.base_url}")
    print(f"Version command: {config.commands.hc_version}")
    print(f"Help command: {config.commands.hc_help}")
    print(f"Book command: {config.commands.book}")
    print(f"Book status command: {config.commands.book_status}")
    print(f"Unbook command: {config.commands.unbook}")
    print(f"Unbook all command: {config.commands.unbook_all}")
