"""JSON value type aliases shared across governed layers."""

from __future__ import annotations

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]

__all__ = [
    "JsonObject",
    "JsonScalar",
    "JsonValue",
]
