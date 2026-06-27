"""Helpers for applying engine-level USI ``go`` defaults."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkRequest
from shogiarena._core.shared.kernel.usi_go_options import (
    GO_OPTION_SEARCH_LIMIT_KEYS,
    coerce_go_option_int,
)


def apply_go_options_defaults(
    request: UsiThinkRequest,
    options: Mapping[str, object] | None,
) -> UsiThinkRequest:
    """Apply engine-level ``go_options`` as defaults without overriding explicit request fields."""

    if not options:
        return request

    replacements: dict[str, object] = {}
    for raw_key, raw_value in options.items():
        key = str(raw_key).strip()
        if key not in GO_OPTION_SEARCH_LIMIT_KEYS:
            expected = ", ".join(sorted(GO_OPTION_SEARCH_LIMIT_KEYS))
            raise ValueError(f"go_options.{key} is not supported; expected one of: {expected}")
        if getattr(request, key) is None:
            replacements[key] = coerce_go_option_int(raw_value, field_name=f"go_options.{key}")

    if not replacements:
        return request
    return replace(request, **replacements)
