"""Validation helpers for engine-level USI ``go`` defaults."""

from __future__ import annotations

from collections.abc import Mapping

from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.serialization import json_serialize

GO_OPTION_SEARCH_LIMIT_KEYS: frozenset[str] = frozenset({"depth", "nodes"})
_UNSUPPORTED_ENGINE_DEFAULT_KEYS: frozenset[str] = frozenset(
    {
        "binc",
        "btime",
        "byoyomi",
        "infinite",
        "is_infinite",
        "movetime",
        "winc",
        "wtime",
    }
)


def normalize_go_options(mapping: Mapping[str, object] | None, *, field_name: str = "go_options") -> JsonObject:
    """Normalize safe engine-level ``go`` defaults and reject timing controls."""

    if mapping is None:
        return {}
    if not isinstance(mapping, Mapping):
        raise TypeError(f"{field_name} must be a mapping")

    normalized: JsonObject = {}
    for raw_key, raw_value in mapping.items():
        key = str(raw_key).strip()
        if key in GO_OPTION_SEARCH_LIMIT_KEYS:
            parsed = coerce_go_option_int(raw_value, field_name=f"{field_name}.{key}")
            if parsed <= 0:
                raise ValueError(f"{field_name}.{key} must be > 0")
            normalized[key] = parsed
            continue
        if key in _UNSUPPORTED_ENGINE_DEFAULT_KEYS:
            raise ValueError(
                f"{field_name}.{key} is not supported as an engine-level default; "
                "use rules.time_control or an explicit search request instead"
            )
        expected = ", ".join(sorted(GO_OPTION_SEARCH_LIMIT_KEYS))
        raise ValueError(f"{field_name}.{key} is not supported; expected one of: {expected}")
    return normalized


def coerce_go_option_int(value: object, *, field_name: str) -> int:
    """Coerce an integer ``go`` option value while rejecting booleans."""

    serialized = json_serialize(value)
    if isinstance(serialized, bool):
        raise TypeError(f"{field_name} must be an integer")
    if isinstance(serialized, int):
        return serialized
    if isinstance(serialized, str):
        stripped = serialized.strip()
        if stripped:
            try:
                return int(stripped)
            except ValueError as exc:
                raise TypeError(f"{field_name} must be an integer") from exc
    raise TypeError(f"{field_name} must be an integer")


__all__ = [
    "GO_OPTION_SEARCH_LIMIT_KEYS",
    "coerce_go_option_int",
    "normalize_go_options",
]
