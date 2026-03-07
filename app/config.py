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

    def __post_init__(self) -> None:
        if not self.hc_version.strip():
            raise ValueError("commands.hc_version must be a non-empty string")
        if not self.hc_version.startswith("!"):
            raise ValueError("commands.hc_version must start with '!' (for example '!hc version')")
        if not self.hc_help.strip():
            raise ValueError("commands.hc_help must be a non-empty string")
        if not self.hc_help.startswith("!"):
            raise ValueError("commands.hc_help must start with '!' (for example '!hc help')")

        version_prefix = self.hc_version.split(" ")[0]
        help_prefix = self.hc_help.split(" ")[0]
        if version_prefix != help_prefix:
            raise ValueError("commands.hc_help must use the same command prefix as commands.hc_version")


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
        hc_help_command = f"{hc_version_command.split(' ')[0]} help"

    commands = CommandsConfig(
        hc_version=hc_version_command,
        hc_help=hc_help_command,
    )

    environments = _environment_mapping(environments_raw, section_name="environments")

    help_config = HelpConfig(
        template=_optional_string(help_raw_any, "template", "help") or DEFAULT_HELP_TEMPLATE,
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
