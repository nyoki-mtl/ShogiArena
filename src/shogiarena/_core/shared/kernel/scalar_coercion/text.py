from __future__ import annotations

from typing import Annotated

from pydantic import BeforeValidator


def coerce_str(value: object | None) -> str | None:
    """Strict: return the trimmed value only when it *is* a ``str``; else ``None``.

    Non-string inputs (``int``, ``Path``, ``bool``, ...) become ``None``. Use this when a
    field must already be a string. This is distinct from :func:`coerce_optional_text`,
    which stringifies any truthy value — the two are intentionally not interchangeable.
    """

    match value:
        case str() as svalue:
            return svalue.strip() or None
        case _:
            return None


def coerce_optional_text(value: object | None) -> str | None:
    """Lenient: stringify any truthy value to trimmed ``str``; empty/falsey becomes ``None``.

    Unlike :func:`coerce_str`, this accepts non-string scalars (e.g. ``123`` -> ``"123"``)
    and is the right choice at lenient I/O boundaries (e.g. the ``OptionalText`` Pydantic type).
    """

    normalized = str(value or "").strip()
    return normalized or None


def coerce_str_list(value: object | tuple[object, ...] | None, *, field: str = "value") -> list[str]:
    """Normalize ``str | list[str] | tuple[str, ...] | None`` into ``list[str]``."""

    match value:
        case None:
            return []
        case str() as svalue:
            stripped = svalue.strip()
            return [stripped] if stripped else []
        case list() | tuple() as items:
            result: list[str] = []
            for item in items:
                match item:
                    case str() as svalue:
                        stripped = svalue.strip()
                        if stripped:
                            result.append(stripped)
                    case _:
                        raise TypeError(f"{field} entries must be strings; got {type(item).__name__}")
            return result
        case _:
            raise TypeError(f"{field} must be a string or list of strings; got {type(value).__name__}")


OptionalText = Annotated[str | None, BeforeValidator(coerce_optional_text)]
"""Pydantic field type that coerces truthy scalars to trimmed ``str``."""


__all__ = ["OptionalText", "coerce_optional_text", "coerce_str", "coerce_str_list"]
