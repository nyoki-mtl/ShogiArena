"""Shared JSON object coercion for interface boundary parsers."""

from __future__ import annotations

from typing import TypeAlias

import yaml

from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize

BoundaryObject: TypeAlias = dict[str, object]


def parse_yaml_value_boundary(raw: str, *, label: str) -> JsonValue:
    """Parse a YAML fragment from CLI and normalize it to JsonValue."""
    if raw == "":
        raise ValueError(f"{label} must not be empty")
    try:
        value = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"failed to parse {label}: {exc}") from exc
    return json_serialize(value)


__all__ = [
    "BoundaryObject",
    "parse_yaml_value_boundary",
]
