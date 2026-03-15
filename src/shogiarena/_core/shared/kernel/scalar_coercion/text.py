from __future__ import annotations

from typing import Annotated

from pydantic import BeforeValidator


def coerce_str(value: object | None) -> str | None:
    """Convert value to trimmed ``str``; empty string becomes ``None``."""

    match value:
        case str() as svalue:
            return svalue.strip() or None
        case _:
            return None


def coerce_optional_text(value: object | None) -> str | None:
    """Convert truthy scalar-ish value to trimmed ``str``; empty/falsey becomes ``None``."""

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
