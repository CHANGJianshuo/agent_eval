"""Small, deterministic template renderer shared by tasks and rubrics.

The project historically accepted both ``{name}`` and ``${name}``, while a
few early tasks also used standalone one-letter placeholders such as ``X``.
Keeping the compatibility handling in one place prevents the SUT prompt and
the grading rubric from being rendered differently.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_DOLLAR_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_BRACE_PLACEHOLDER = re.compile(r"(?<!\$)\{([A-Za-z_][A-Za-z0-9_]*)\}")


class MissingTemplateVariables(ValueError):
    """Raised when a template references variables that were not provided."""

    def __init__(self, names: set[str]):
        self.names = frozenset(names)
        super().__init__(f"缺少模板变量: {', '.join(sorted(names))}")


def placeholder_names(text: str) -> set[str]:
    """Return named placeholders used by either supported syntax."""
    return {
        *_DOLLAR_PLACEHOLDER.findall(text),
        *_BRACE_PLACEHOLDER.findall(text),
    }


def render_template(text: str, variables: Mapping[str, Any], *,
                    allow_legacy_letters: bool = True) -> str:
    """Render a task/rubric template and fail if a named value is missing.

    ``{name}`` is the canonical syntax. ``${name}`` and standalone uppercase
    one-letter variables are retained for compatibility with imported tasks.
    Only simple identifiers are interpreted, so ordinary JSON/CSS braces are
    left untouched.
    """
    missing: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            missing.add(name)
            return match.group(0)
        return str(variables[name])

    rendered = _DOLLAR_PLACEHOLDER.sub(replace, text)
    rendered = _BRACE_PLACEHOLDER.sub(replace, rendered)

    if allow_legacy_letters:
        for name, value in variables.items():
            if re.fullmatch(r"[A-Z]", name):
                rendered = re.sub(
                    rf"(?<![A-Za-z0-9_{{]){re.escape(name)}"
                    rf"(?![A-Za-z0-9_}}])",
                    str(value),
                    rendered,
                )

    if missing:
        raise MissingTemplateVariables(missing)
    return rendered
