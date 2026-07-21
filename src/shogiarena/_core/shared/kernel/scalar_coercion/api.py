"""Scalar coercion helpers shared across governed layers."""

from __future__ import annotations

from shogiarena._core.shared.kernel.game_result_coercion import coerce_game_result
from shogiarena._core.shared.kernel.scalar_coercion.boolean import coerce_bool, coerce_optional_bool
from shogiarena._core.shared.kernel.scalar_coercion.mapping import coerce_float_dict, coerce_nested_float_dict
from shogiarena._core.shared.kernel.scalar_coercion.numeric import (
    coerce_float,
    coerce_int,
    coerce_int_strict,
    coerce_non_negative_int,
    coerce_non_negative_int_strict,
    coerce_optional_int_strict,
    coerce_optional_non_negative_int_strict,
    is_strict_numeric,
    strict_int,
)
from shogiarena._core.shared.kernel.scalar_coercion.temporal import (
    coerce_iso_datetime,
    coerce_timestamp_ms,
    datetime_to_iso,
    timestamp_to_iso,
)
from shogiarena._core.shared.kernel.scalar_coercion.text import (
    OptionalText,
    coerce_optional_text,
    coerce_str,
    coerce_str_list,
)

__all__ = [
    "coerce_bool",
    "coerce_optional_bool",
    "coerce_float",
    "coerce_float_dict",
    "coerce_game_result",
    "coerce_iso_datetime",
    "coerce_int",
    "coerce_int_strict",
    "coerce_non_negative_int",
    "coerce_nested_float_dict",
    "coerce_non_negative_int_strict",
    "coerce_optional_int_strict",
    "coerce_optional_non_negative_int_strict",
    "OptionalText",
    "coerce_optional_text",
    "coerce_str",
    "coerce_str_list",
    "coerce_timestamp_ms",
    "datetime_to_iso",
    "is_strict_numeric",
    "strict_int",
    "timestamp_to_iso",
]
