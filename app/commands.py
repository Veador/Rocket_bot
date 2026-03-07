"""Command parsing for the Rocket.Chat bot.

Supported format:
    !help
    !hc version <alias>
    !book <alias> <time>
    !book status <alias>
    !unbook <alias>
    !unbook all
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import BotConfig, DEFAULT_CONFIG_PATH, EnvironmentConfig, load_config


@dataclass(slots=True)
class ParsedCommand:
    """Parsed command result for command handler consumption."""

    kind: Literal["help", "version", "book", "book_status", "unbook", "unbook_all"]
    command: str
    alias: str
    url: str | None
    duration: str | None = None
    environment_name: str | None = None
    error_message: str | None = None

    @property
    def is_error(self) -> bool:
        return self.error_message is not None


class CommandParser:
    """Strict parser for configured commands."""

    def __init__(
        self,
        *,
        version_command_text: str,
        help_command_text: str,
        book_command_text: str,
        book_status_command_text: str,
        unbook_command_text: str,
        unbook_all_command_text: str,
        environments: dict[str, EnvironmentConfig],
    ) -> None:
        self.version_command_text = _normalize_spaces(version_command_text)
        if not self.version_command_text:
            raise ValueError("commands.hc_version must be a non-empty string")
        self.help_command_text = _normalize_spaces(help_command_text)
        if not self.help_command_text:
            raise ValueError("commands.hc_help must be a non-empty string")
        self.book_command_text = _normalize_spaces(book_command_text)
        if not self.book_command_text:
            raise ValueError("commands.book must be a non-empty string")
        self.book_status_command_text = _normalize_spaces(book_status_command_text)
        if not self.book_status_command_text:
            raise ValueError("commands.book_status must be a non-empty string")
        self.unbook_command_text = _normalize_spaces(unbook_command_text)
        if not self.unbook_command_text:
            raise ValueError("commands.unbook must be a non-empty string")
        self.unbook_all_command_text = _normalize_spaces(unbook_all_command_text)
        if not self.unbook_all_command_text:
            raise ValueError("commands.unbook_all must be a non-empty string")

        self.command_text = self.version_command_text  # Backward-compatible alias.
        self.command_tokens = self.version_command_text.split(" ")
        self.command_prefix = self.command_tokens[0]
        self.help_command_tokens = self.help_command_text.split(" ")
        self.book_command_tokens = self.book_command_text.split(" ")
        self.book_status_command_tokens = self.book_status_command_text.split(" ")
        self.unbook_command_tokens = self.unbook_command_text.split(" ")
        self.unbook_all_command_tokens = self.unbook_all_command_text.split(" ")

        if self.book_status_command_tokens[0] != self.book_command_tokens[0]:
            raise ValueError("commands.book_status must use the same command prefix as commands.book")
        if self.unbook_all_command_tokens[0] != self.unbook_command_tokens[0]:
            raise ValueError("commands.unbook_all must use the same command prefix as commands.unbook")

        self.command_prefixes = {
            self.command_prefix,
            self.help_command_tokens[0],
            self.book_command_tokens[0],
            self.unbook_command_tokens[0],
            self.unbook_all_command_tokens[0],
        }
        self.environments = dict(environments)

    @classmethod
    def from_config(cls, config: BotConfig) -> CommandParser:
        """Build parser from loaded bot config."""
        return cls(
            version_command_text=config.commands.hc_version,
            help_command_text=config.commands.hc_help,
            book_command_text=config.commands.book,
            book_status_command_text=config.commands.book_status,
            unbook_command_text=config.commands.unbook,
            unbook_all_command_text=config.commands.unbook_all,
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
        if not tokens or tokens[0] not in self.command_prefixes:
            return None

        if tokens == self.help_command_tokens:
            return ParsedCommand(
                kind="help",
                command=self.help_command_text,
                alias="",
                url=None,
                duration=None,
                environment_name=None,
                error_message=None,
            )

        # !book status <alias>
        if tokens[: len(self.book_status_command_tokens)] == self.book_status_command_tokens:
            if len(tokens) > len(self.book_status_command_tokens) + 1:
                return None
            alias = tokens[len(self.book_status_command_tokens)] if len(tokens) > len(
                self.book_status_command_tokens
            ) else ""
            return ParsedCommand(
                kind="book_status",
                command=self.book_status_command_text,
                alias=alias,
                url=None,
                duration=None,
                environment_name=None,
                error_message=None,
            )

        # !unbook all
        if tokens == self.unbook_all_command_tokens:
            return ParsedCommand(
                kind="unbook_all",
                command=self.unbook_all_command_text,
                alias="all",
                url=None,
                duration=None,
                environment_name=None,
                error_message=None,
            )

        # !unbook <alias>
        if tokens[: len(self.unbook_command_tokens)] == self.unbook_command_tokens:
            if len(tokens) > len(self.unbook_command_tokens) + 1:
                return None
            alias = tokens[len(self.unbook_command_tokens)] if len(tokens) > len(
                self.unbook_command_tokens
            ) else ""
            return ParsedCommand(
                kind="unbook",
                command=self.unbook_command_text,
                alias=alias,
                url=None,
                duration=None,
                environment_name=None,
                error_message=None,
            )

        # !book <alias> <time>
        if tokens[: len(self.book_command_tokens)] == self.book_command_tokens:
            if len(tokens) > len(self.book_command_tokens) + 2:
                return None
            alias = tokens[len(self.book_command_tokens)] if len(tokens) > len(
                self.book_command_tokens
            ) else ""
            duration = tokens[len(self.book_command_tokens) + 1] if len(tokens) > len(
                self.book_command_tokens
            ) + 1 else ""
            return ParsedCommand(
                kind="book",
                command=self.book_command_text,
                alias=alias,
                url=None,
                duration=duration,
                environment_name=None,
                error_message=None,
            )

        # !hc version <alias>
        if tokens[: len(self.command_tokens)] != self.command_tokens:
            return None
        if len(tokens) != len(self.command_tokens) + 1:
            return None

        alias = tokens[-1]
        environment = self.environments.get(alias)
        if environment is None:
            return ParsedCommand(
                kind="version",
                command=self.command_text,
                alias=alias,
                url=None,
                duration=None,
                environment_name=None,
                error_message=f"Unknown environment alias: {alias}",
            )

        return ParsedCommand(
            kind="version",
            command=self.command_text,
            alias=alias,
            url=environment.url,
            duration=None,
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
