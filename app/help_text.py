"""Help message rendering helpers for `!hc help`."""

from __future__ import annotations

from collections.abc import Mapping

from app.config import EnvironmentConfig


def build_commands_list(
    *,
    version_command: str,
    help_command: str,
) -> str:
    """Return newline-separated list of supported commands."""
    return "\n".join(
        [
            help_command,
            f"{version_command} <alias>",
        ]
    )


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
    environments: Mapping[str, EnvironmentConfig],
) -> str:
    """Render help message by replacing supported placeholders."""
    commands_block = build_commands_list(
        version_command=version_command,
        help_command=help_command,
    )
    aliases_block = build_aliases_list(environments)

    return (
        template.replace("{{commands}}", commands_block)
        .replace("{{aliases}}", aliases_block)
        .strip()
    )
