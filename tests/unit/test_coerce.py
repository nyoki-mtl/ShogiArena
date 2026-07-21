"""utils.types.coerce モジュールのテスト。"""

from __future__ import annotations

import pytest
from rsshogi.record import GameResult

from shogiarena._core.shared.kernel.scalar_coercion.api import (
    coerce_bool,
    coerce_float,
    coerce_game_result,
    coerce_int,
    coerce_int_strict,
    coerce_optional_bool,
    coerce_str,
    coerce_str_list,
    coerce_timestamp_ms,
    timestamp_to_iso,
)

# ── coerce_int ──────────────────────────────────────────────────


class TestCoerceInt:
    def test_int(self) -> None:
        assert coerce_int(42) == 42
        assert coerce_int(-1) == -1
        assert coerce_int(0) == 0

    def test_large_int(self) -> None:
        assert coerce_int(10**18) == 10**18
        assert coerce_int(-(10**18)) == -(10**18)

    def test_bool_rejected(self) -> None:
        assert coerce_int(True) is None
        assert coerce_int(False) is None

    def test_float_truncates(self) -> None:
        assert coerce_int(3.9) == 3
        assert coerce_int(-1.1) == -1

    def test_float_zero(self) -> None:
        assert coerce_int(0.0) == 0
        assert coerce_int(-0.0) == 0

    def test_float_infinite(self) -> None:
        assert coerce_int(float("inf")) is None
        assert coerce_int(float("-inf")) is None
        assert coerce_int(float("nan")) is None

    def test_str(self) -> None:
        assert coerce_int("42") == 42
        assert coerce_int(" -7 ") == -7

    def test_str_zero(self) -> None:
        assert coerce_int("0") == 0

    def test_str_float_format_rejected(self) -> None:
        assert coerce_int("3.14") is None
        assert coerce_int("1e5") is None

    def test_str_invalid(self) -> None:
        assert coerce_int("abc") is None
        assert coerce_int("") is None
        assert coerce_int("  ") is None

    def test_none(self) -> None:
        assert coerce_int(None) is None

    def test_other_types(self) -> None:
        assert coerce_int([1]) is None
        assert coerce_int({"a": 1}) is None
        assert coerce_int((1,)) is None
        assert coerce_int(object()) is None


# ── coerce_int_strict ───────────────────────────────────────────


class TestCoerceIntStrict:
    def test_valid(self) -> None:
        assert coerce_int_strict(42) == 42
        assert coerce_int_strict("10", "count") == 10

    def test_raises_on_bool(self) -> None:
        with pytest.raises(ValueError, match="int に変換できません"):
            coerce_int_strict(True)

    def test_raises_on_none(self) -> None:
        with pytest.raises(ValueError, match="int に変換できません"):
            coerce_int_strict(None, "field")

    def test_raises_on_invalid_str(self) -> None:
        with pytest.raises(ValueError):
            coerce_int_strict("abc")

    def test_field_name_in_error_message(self) -> None:
        with pytest.raises(ValueError, match="myfield"):
            coerce_int_strict(None, "myfield")

    def test_raises_on_float_inf(self) -> None:
        with pytest.raises(ValueError):
            coerce_int_strict(float("inf"))


# ── coerce_float ────────────────────────────────────────────────


class TestCoerceFloat:
    def test_int_to_float(self) -> None:
        assert coerce_float(42) == 42.0
        assert isinstance(coerce_float(42), float)

    def test_float(self) -> None:
        assert coerce_float(3.14) == 3.14

    def test_float_zero(self) -> None:
        assert coerce_float(0) == 0.0
        assert coerce_float(0.0) == 0.0
        assert coerce_float(-0.0) == 0.0

    def test_bool_rejected(self) -> None:
        assert coerce_float(True) is None
        assert coerce_float(False) is None

    def test_infinite(self) -> None:
        assert coerce_float(float("inf")) is None
        assert coerce_float(float("-inf")) is None
        assert coerce_float(float("nan")) is None

    def test_str(self) -> None:
        assert coerce_float("3.14") == 3.14
        assert coerce_float(" -1.5 ") == -1.5

    def test_str_zero(self) -> None:
        assert coerce_float("0") == 0.0
        assert coerce_float("0.0") == 0.0

    def test_str_scientific_notation(self) -> None:
        assert coerce_float("1e5") == 100000.0
        assert coerce_float("-2.5e3") == -2500.0

    def test_str_non_finite_rejected(self) -> None:
        assert coerce_float("inf") is None
        assert coerce_float("-inf") is None
        assert coerce_float("nan") is None
        assert coerce_float("NaN") is None

    def test_str_invalid(self) -> None:
        assert coerce_float("abc") is None
        assert coerce_float("") is None
        assert coerce_float("  ") is None

    def test_none(self) -> None:
        assert coerce_float(None) is None

    def test_other_types(self) -> None:
        assert coerce_float([1.0]) is None
        assert coerce_float({"a": 1.0}) is None
        assert coerce_float(object()) is None


# ── coerce_bool ─────────────────────────────────────────────────


class TestCoerceBool:
    def test_bool(self) -> None:
        assert coerce_bool(True) is True
        assert coerce_bool(False) is False

    def test_int(self) -> None:
        assert coerce_bool(1) is True
        assert coerce_bool(0) is False
        assert coerce_bool(-1) is True
        assert coerce_bool(42) is True

    def test_float(self) -> None:
        assert coerce_bool(0.0) is False
        assert coerce_bool(1.0) is True
        assert coerce_bool(0.5) is True

    def test_truthy_strings(self) -> None:
        for s in ("1", "true", "True", "TRUE", "t", "yes", "YES", "y", "on", "ON"):
            assert coerce_bool(s) is True, f"Expected True for {s!r}"

    def test_truthy_strings_with_whitespace(self) -> None:
        assert coerce_bool(" true ") is True
        assert coerce_bool("  1  ") is True

    def test_falsy_strings(self) -> None:
        for s in ("0", "false", "False", "FALSE", "f", "no", "NO", "n", "off", "OFF"):
            assert coerce_bool(s) is False, f"Expected False for {s!r}"

    def test_unrecognized_string(self) -> None:
        assert coerce_bool("maybe") is False
        assert coerce_bool("") is False

    def test_none(self) -> None:
        assert coerce_bool(None) is False

    def test_other_types(self) -> None:
        assert coerce_bool([]) is False
        assert coerce_bool({}) is False
        assert coerce_bool(object()) is False


class TestCoerceOptionalBool:
    def test_known_values(self) -> None:
        assert coerce_optional_bool(True) is True
        assert coerce_optional_bool(False) is False
        assert coerce_optional_bool("yes") is True
        assert coerce_optional_bool("off") is False

    def test_unknown_values_stay_none(self) -> None:
        assert coerce_optional_bool(None) is None
        assert coerce_optional_bool("maybe") is None
        assert coerce_optional_bool(object()) is None


# ── coerce_str ──────────────────────────────────────────────────


class TestCoerceStr:
    def test_normal(self) -> None:
        assert coerce_str("hello") == "hello"
        assert coerce_str("  padded  ") == "padded"

    def test_empty(self) -> None:
        assert coerce_str("") is None
        assert coerce_str("   ") is None

    def test_whitespace_variants(self) -> None:
        assert coerce_str("\t") is None
        assert coerce_str("\n") is None
        assert coerce_str(" \t\n ") is None

    def test_non_string(self) -> None:
        assert coerce_str(42) is None
        assert coerce_str(None) is None
        assert coerce_str(True) is None
        assert coerce_str(3.14) is None
        assert coerce_str([]) is None


# ── coerce_timestamp_ms ─────────────────────────────────────────


class TestCoerceTimestampMs:
    def test_int(self) -> None:
        assert coerce_timestamp_ms(1000) == 1000

    def test_int_zero(self) -> None:
        assert coerce_timestamp_ms(0) == 0

    def test_int_negative(self) -> None:
        assert coerce_timestamp_ms(-1000) == -1000

    def test_float(self) -> None:
        assert coerce_timestamp_ms(1000.5) == 1000

    def test_bool_rejected(self) -> None:
        assert coerce_timestamp_ms(True) is None
        assert coerce_timestamp_ms(False) is None

    def test_numeric_string(self) -> None:
        assert coerce_timestamp_ms("1000") == 1000

    def test_float_string(self) -> None:
        assert coerce_timestamp_ms("1000.5") == 1000

    def test_iso_string(self) -> None:
        result = coerce_timestamp_ms("2024-01-01T00:00:00Z")
        assert result is not None
        assert isinstance(result, int)

    def test_iso_string_with_timezone_offset(self) -> None:
        result = coerce_timestamp_ms("2024-01-01T09:00:00+09:00")
        assert result is not None
        assert isinstance(result, int)

    def test_invalid(self) -> None:
        assert coerce_timestamp_ms("not-a-date") is None
        assert coerce_timestamp_ms("") is None
        assert coerce_timestamp_ms("  ") is None
        assert coerce_timestamp_ms(None) is None

    def test_float_nan(self) -> None:
        assert coerce_timestamp_ms(float("nan")) is None

    def test_float_inf(self) -> None:
        assert coerce_timestamp_ms(float("inf")) is None
        assert coerce_timestamp_ms(float("-inf")) is None

    def test_str_nan_inf(self) -> None:
        assert coerce_timestamp_ms("nan") is None
        assert coerce_timestamp_ms("inf") is None
        assert coerce_timestamp_ms("-inf") is None

    def test_other_types(self) -> None:
        assert coerce_timestamp_ms([1000]) is None
        assert coerce_timestamp_ms({"ts": 1000}) is None


# ── timestamp_to_iso ────────────────────────────────────────────


class TestTimestampToIso:
    def test_valid(self) -> None:
        result = timestamp_to_iso(0)
        assert result is not None
        assert "1970" in result

    def test_float_value(self) -> None:
        result = timestamp_to_iso(1000.0)
        assert result is not None
        assert "1970" in result

    def test_bool_rejected(self) -> None:
        assert timestamp_to_iso(True) is None
        assert timestamp_to_iso(False) is None

    def test_overflow(self) -> None:
        assert timestamp_to_iso(10**20) is None

    def test_non_numeric(self) -> None:
        assert timestamp_to_iso("abc") is None
        assert timestamp_to_iso(None) is None
        assert timestamp_to_iso([]) is None


# ── coerce_game_result ──────────────────────────────────────────


class TestCoerceGameResult:
    def test_passthrough(self) -> None:
        gr = GameResult.BLACK_WIN
        assert coerce_game_result(gr) is gr

    def test_from_int(self) -> None:
        # GameResult(1) should be a valid result
        result = coerce_game_result(1)
        assert isinstance(result, GameResult)

    def test_from_str(self) -> None:
        result = coerce_game_result("1")
        assert isinstance(result, GameResult)

    def test_from_str_with_whitespace(self) -> None:
        result = coerce_game_result(" 1 ")
        assert isinstance(result, GameResult)

    def test_from_lowercase_name_with_whitespace(self) -> None:
        assert coerce_game_result(" black_win ") == GameResult.BLACK_WIN

    def test_from_str_empty_after_strip(self) -> None:
        assert coerce_game_result("   ") is None

    def test_bool_rejected(self) -> None:
        assert coerce_game_result(True) is None
        assert coerce_game_result(False) is None

    def test_float_rejected(self) -> None:
        assert coerce_game_result(1.0) is None
        assert coerce_game_result(3.14) is None

    def test_invalid_returns_none(self) -> None:
        assert coerce_game_result(None) is None
        assert coerce_game_result("abc") is None
        assert coerce_game_result("DRAW") is None
        assert coerce_game_result("") is None
        assert coerce_game_result([]) is None
        assert coerce_game_result({}) is None

    def test_strict_raises(self) -> None:
        with pytest.raises(ValueError):
            coerce_game_result(None, is_strict=True)
        with pytest.raises(ValueError):
            coerce_game_result(True, is_strict=True)
        with pytest.raises(ValueError):
            coerce_game_result("abc", is_strict=True)
        with pytest.raises(ValueError):
            coerce_game_result("DRAW", is_strict=True)
        with pytest.raises(ValueError):
            coerce_game_result("", is_strict=True)

    def test_strict_raises_on_float(self) -> None:
        with pytest.raises(ValueError):
            coerce_game_result(1.0, is_strict=True)

    def test_strict_valid(self) -> None:
        result = coerce_game_result(1, is_strict=True)
        assert isinstance(result, GameResult)

    def test_strict_valid_from_str(self) -> None:
        result = coerce_game_result("1", is_strict=True)
        assert isinstance(result, GameResult)

    def test_strict_valid_from_lowercase_name(self) -> None:
        assert coerce_game_result("draw_by_repetition", is_strict=True) == GameResult.DRAW_BY_REPETITION

    def test_invalid_int(self) -> None:
        # Very large int unlikely to be a valid GameResult
        assert coerce_game_result(999999) is None

    def test_strict_invalid_int(self) -> None:
        with pytest.raises(ValueError):
            coerce_game_result(999999, is_strict=True)


# ── coerce_str_list ────────────────────────────────────────────


class TestCoerceStrList:
    def test_none_returns_empty(self) -> None:
        assert coerce_str_list(None) == []

    def test_single_string(self) -> None:
        assert coerce_str_list("hello") == ["hello"]

    def test_string_stripped(self) -> None:
        assert coerce_str_list("  hello  ") == ["hello"]

    def test_empty_string_returns_empty(self) -> None:
        assert coerce_str_list("") == []

    def test_whitespace_only_string_returns_empty(self) -> None:
        assert coerce_str_list("   ") == []
        assert coerce_str_list("\t\n") == []

    def test_list_of_strings(self) -> None:
        assert coerce_str_list(["a", "b", "c"]) == ["a", "b", "c"]

    def test_list_strips_items(self) -> None:
        assert coerce_str_list(["  a  ", " b "]) == ["a", "b"]

    def test_list_skips_empty_items(self) -> None:
        assert coerce_str_list(["a", "", "  ", "b"]) == ["a", "b"]

    def test_tuple_of_strings(self) -> None:
        assert coerce_str_list(("x", "y")) == ["x", "y"]

    def test_tuple_strips_and_skips(self) -> None:
        assert coerce_str_list(("  a  ", "", "b")) == ["a", "b"]

    def test_empty_list(self) -> None:
        assert coerce_str_list([]) == []

    def test_empty_tuple(self) -> None:
        assert coerce_str_list(()) == []

    def test_list_with_non_string_raises(self) -> None:
        with pytest.raises(TypeError, match="entries must be strings"):
            coerce_str_list(["a", 123])

    def test_list_with_none_item_raises(self) -> None:
        with pytest.raises(TypeError, match="entries must be strings"):
            coerce_str_list(["a", None])

    def test_int_raises(self) -> None:
        with pytest.raises(TypeError, match="must be a string or list of strings"):
            coerce_str_list(42)

    def test_dict_raises(self) -> None:
        with pytest.raises(TypeError, match="must be a string or list of strings"):
            coerce_str_list({"a": 1})

    def test_bool_raises(self) -> None:
        with pytest.raises(TypeError, match="must be a string or list of strings"):
            coerce_str_list(True)

    def test_field_name_in_error(self) -> None:
        with pytest.raises(TypeError, match="overlays"):
            coerce_str_list(42, field="overlays")

    def test_field_name_in_entry_error(self) -> None:
        with pytest.raises(TypeError, match="paths"):
            coerce_str_list([1], field="paths")
