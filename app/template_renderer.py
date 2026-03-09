"""Shared lightweight renderer for config-driven message templates."""

from __future__ import annotations

from collections.abc import Mapping
import re

_PLACEHOLDER_RE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


def render_template(template: str, context: Mapping[str, object]) -> str:
    """Render `{{variable}}` placeholders using context values.

    Behavior for missing variables is predictable: unknown placeholders are left as-is.
    """
    if not isinstance(template, str):
        raise ValueError("template must be a string")

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            return match.group(0)
        value = context.get(key)
        return "" if value is None else str(value)

    return _PLACEHOLDER_RE.sub(_replace, template).strip()
