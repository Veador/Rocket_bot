"""Command parsing for the Rocket.Chat PoC bot.

Supported format:
    !hc help
    !hc version <alias>

Behavior:
- command texts come from config (`commands.hc_version`, `commands.hc_help`)
- aliases are resolved from config (`environments`)
- unsupported commands return None
- unknown alias returns parsed command object with error message
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import BotConfig, DEFAULT_CONFIG_PATH, EnvironmentConfig, load_config


@dataclass(slots=True)
class ParsedCommand:
    """Parsed command result for command handler consumption."""

    kind: Literal["help", "version"]
    command: str
    alias: str
    url: str | None
    environment_name: str | None = None
    error_message: str | None = None

    @property
    def is_error(self) -> bool:
        return self.error_message is not None


class CommandParser:
    """Strict parser for configured `!hc version <alias>` command."""

    def __init__(
        self,
        *,
        version_command_text: str,
        help_command_text: str,
        environments: dict[str, EnvironmentConfig],
    ) -> None:
        normalized_version_command = _normalize_spaces(version_command_text)
        if not normalized_version_command:
            raise ValueError("commands.hc_version must be a non-empty string")
        normalized_help_command = _normalize_spaces(help_command_text)
        if not normalized_help_command:
            raise ValueError("commands.hc_help must be a non-empty string")

        self.command_text = normalized_version_command
        self.command_tokens = normalized_version_command.split(" ")
        self.command_prefix = self.command_tokens[0]
        self.help_command_text = normalized_help_command
        self.help_command_tokens = normalized_help_command.split(" ")
        if self.help_command_tokens[0] != self.command_prefix:
            raise ValueError("commands.hc_help must use the same command prefix as commands.hc_version")
        self.environments = dict(environments)

    @classmethod
    def from_config(cls, config: BotConfig) -> CommandParser:
        """Build parser from loaded bot config."""
        return cls(
            version_command_text=config.commands.hc_version,
            help_command_text=config.commands.hc_help,
            environments=config.environments,
        )

    @classmethod
    def from_config_file(
        cls,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
    ) -> CommandParser:
        """Build parser directly from config file."""
        config = load_config(config_path)
        return cls.from_config(config)

    def parse_message(self, raw_text: str) -> ParsedCommand | None:
        """Parse raw message text into a command object or None.

        Returns:
            ParsedCommand: for supported command format; may include alias error.
            None: unsupported command or invalid shape.
        """
        normalized_text = _normalize_spaces(raw_text)
        if not normalized_text:
            return None

        tokens = normalized_text.split(" ")
        if not tokens or tokens[0] != self.command_prefix:
            return None

        if tokens == self.help_command_tokens:
            return ParsedCommand(
                kind="help",
                command=self.help_command_text,
                alias="",
                url=None,
                environment_name=None,
                error_message=None,
            )

        # Strict PoC: exact command tokens + exactly one alias token.
        if len(tokens) != len(self.command_tokens) + 1:
            return None

        if tokens[: len(self.command_tokens)] != self.command_tokens:
            return None

        alias = tokens[-1]
        environment = self.environments.get(alias)
        if environment is None:
            return ParsedCommand(
                kind="version",
                command=self.command_text,
                alias=alias,
                url=None,
                environment_name=None,
                error_message=f"Unknown environment alias: {alias}",
            )

        return ParsedCommand(
            kind="version",
            command=self.command_text,
            alias=alias,
            url=environment.url,
            environment_name=environment.name or alias,
            error_message=None,
        )


def parse_command_text(
    raw_text: str,
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
) -> ParsedCommand | None:
    """Convenience parser: parse one raw message using config-backed parser."""
    parser = CommandParser.from_config_file(config_path)
    return parser.parse_message(raw_text)


def _normalize_spaces(text: str) -> str:
    if not isinstance(text, str):
        return ""
    return " ".join(text.split())
