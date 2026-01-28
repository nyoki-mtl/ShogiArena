"""Slot estimation policy helpers for instance scheduling."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

__all__ = ["estimate_required_slots"]

_TRUE_VALUES = {"true", "1", "yes", "on"}
_FALSE_VALUES = {"false", "0", "no", "off"}


def _extract_int_option(options: Mapping[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        if key not in options:
            continue
        value = options[key]
        if isinstance(value, bool):
            continue
        try:
            # Handles int, float-as-int, and numeric strings
            numeric = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if numeric > 0:
            return numeric
    return None


def _extract_bool_option(options: Mapping[str, Any], keys: tuple[str, ...]) -> bool | None:
    for key in keys:
        if key not in options:
            continue
        value = options[key]
        if isinstance(value, bool):
            return value
        if value is None:
            continue
        normalized = str(value).strip().lower()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
    return None


def estimate_required_slots(engine_spec: Any, extra_options: Mapping[str, Any] | None = None) -> int:
    """
    Estimate how many slots an engine should reserve on its target instance.

    Threads/USI_Threads determine the baseline slot demand. When pondering is
    disabled (no `USI_Ponder`/`Ponder` true flag), engines consume CPU only on
    their active turn, so we conservatively reserve half the threads (rounded
    up) to allow both sides to coexist on the same hardware without exceeding
    configured slots. When pondering is enabled we reserve the full thread
    count.
    """

    option_sources: list[Mapping[str, Any]] = []
    if isinstance(extra_options, Mapping):
        option_sources.append(extra_options)
    spec_options = getattr(engine_spec, "options", None)
    if isinstance(spec_options, Mapping):
        option_sources.append(spec_options)

    threads: int | None = None
    for options in option_sources:
        extracted = _extract_int_option(options, ("Threads", "USI_Threads"))
        if extracted is not None:
            threads = extracted
            break

    if threads is None or threads <= 0:
        threads = 1

    ponder_enabled: bool | None = None
    for options in option_sources:
        extracted_bool = _extract_bool_option(options, ("USI_Ponder", "Ponder"))
        if extracted_bool is not None:
            ponder_enabled = extracted_bool
            break

    if ponder_enabled:
        required = threads
    else:
        required = max(1, math.ceil(threads * 0.5))

    return required
