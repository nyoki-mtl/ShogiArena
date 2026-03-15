"""Slot estimation policy helpers for instance scheduling."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Protocol

from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_optional_text
from shogiarena._core.shared.kernel.serialization import json_serialize

__all__ = ["OptionsPort", "estimate_required_slots"]

_TRUE_VALUES = {"true", "1", "yes", "on"}
_FALSE_VALUES = {"false", "0", "no", "off"}


class OptionsPort(Protocol):
    options: Mapping[str, JsonValue] | None


def _extract_int_option(options: Mapping[str, JsonValue], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        if key not in options:
            continue
        value = options[key]
        numeric = coerce_int(value)
        if numeric is None:
            continue
        if numeric > 0:
            return numeric
    return None


def _extract_bool_option(options: Mapping[str, JsonValue], keys: tuple[str, ...]) -> bool | None:
    for key in keys:
        if key not in options:
            continue
        value = options[key]
        if isinstance(value, bool):
            return value
        normalized = coerce_optional_text(value)
        if normalized is None:
            continue
        normalized = normalized.lower()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
    return None


def estimate_required_slots(engine_spec: OptionsPort, extra_options: Mapping[str, JsonValue] | None = None) -> int:
    """
    Estimate how many slots an engine should reserve on its target instance.

    Threads/USI_Threads determine the baseline slot demand. When pondering is
    disabled (no `USI_Ponder`/`Ponder` true flag), engines consume CPU only on
    their active turn, so we conservatively reserve half the threads (rounded
    up) to allow both sides to coexist on the same hardware without exceeding
    configured slots. When pondering is enabled we reserve the full thread
    count.
    """

    option_sources: list[Mapping[str, JsonValue]] = []
    if extra_options is not None:
        option_sources.append(extra_options)
    spec_options_raw = engine_spec.options
    if spec_options_raw is not None:
        spec_options: dict[str, JsonValue] = {
            str(key): json_serialize(value) for key, value in spec_options_raw.items()
        }
        option_sources.append(spec_options)

    threads: int | None = None
    for options in option_sources:
        extracted = _extract_int_option(options, ("Threads", "USI_Threads"))
        if extracted is not None:
            threads = extracted
            break

    if threads is None or threads <= 0:
        threads = 1

    is_ponder_enabled: bool | None = None
    for options in option_sources:
        extracted_bool = _extract_bool_option(options, ("USI_Ponder", "Ponder"))
        if extracted_bool is not None:
            is_ponder_enabled = extracted_bool
            break

    if is_ponder_enabled:
        required = threads
    else:
        required = max(1, math.ceil(threads * 0.5))

    return required
