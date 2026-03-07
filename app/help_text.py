"""Help message rendering helpers for `!help`."""

from __future__ import annotations

from collections.abc import Mapping

from app.config import EnvironmentConfig


def build_commands_list(
    *,
    version_command: str,
    help_command: str,
    book_command: str | None = None,
    book_status_command: str | None = None,
    unbook_command: str | None = None,
    unbook_all_command: str | None = None,
) -> str:
    """Return newline-separated list of supported commands."""
    commands = [
        help_command,
        f"{version_command} <alias>",
    ]
    if book_command:
        commands.append(f"{book_command} <alias> <time>")
    if book_status_command:
        commands.append(f"{book_status_command} <alias>")
    if unbook_command:
        commands.append(f"{unbook_command} <alias>")
    if unbook_all_command:
        commands.append(unbook_all_command)
    return "\n".join(commands)


def build_aliases_list(environments: Mapping[str, EnvironmentConfig]) -> str:
    """Return newline-separated alias list from config."""
    aliases = sorted(environments.keys())
    if not aliases:
        return "-"
    return "\n".join(aliases)


def render_help_message(
    *,
    template: str,
    version_command: str,
    help_command: str,
    book_command: str | None = None,
    book_status_command: str | None = None,
    unbook_command: str | None = None,
    unbook_all_command: str | None = None,
    environments: Mapping[str, EnvironmentConfig],
) -> str:
    """Render help message by replacing supported placeholders."""
    commands_block = build_commands_list(
        version_command=version_command,
        help_command=help_command,
        book_command=book_command,
        book_status_command=book_status_command,
        unbook_command=unbook_command,
        unbook_all_command=unbook_all_command,
    )
    aliases_block = build_aliases_list(environments)

    return (
        template.replace("{{commands}}", commands_block)
        .replace("{{aliases}}", aliases_block)
        .strip()
    )
