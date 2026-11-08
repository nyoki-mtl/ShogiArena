"""scalar_coercion モジュールのテスト。"""

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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (42, 42),
        (-1, -1),
        (0, 0),
        (10**18, 10**18),
        (-(10**18), -(10**18)),
        (True, None),
        (False, None),
        (3.9, 3),
        (-1.1, -1),
        (0.0, 0),
        (-0.0, 0),
        (float("inf"), None),
        (float("-inf"), None),
        (float("nan"), None),
        ("42", 42),
        (" -7 ", -7),
        ("0", 0),
        ("3.14", None),
        ("1e5", None),
        ("abc", None),
        ("", None),
        ("  ", None),
        (None, None),
        ([1], None),
        ({"a": 1}, None),
    ],
)
def test_coerce_int(raw: object, expected: int | None) -> None:
    assert coerce_int(raw) == expected


def test_coerce_int_strict_valid_and_invalid() -> None:
    assert coerce_int_strict(42) == 42
    assert coerce_int_strict("10", "count") == 10
    for invalid in (True, None, "abc", float("inf")):
        with pytest.raises(ValueError, match="int に変換できません"):
            coerce_int_strict(invalid, "myfield")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (42, 42.0),
        (3.14, 3.14),
        (0, 0.0),
        (-0.0, 0.0),
        (True, None),
        (False, None),
        (float("inf"), None),
        (float("-inf"), None),
        (float("nan"), None),
        ("3.14", 3.14),
        (" -1.5 ", -1.5),
        ("0", 0.0),
        ("1e5", 100000.0),
        ("-2.5e3", -2500.0),
        ("inf", None),
        ("-inf", None),
        ("nan", None),
        ("NaN", None),
        ("abc", None),
        ("", None),
        ("  ", None),
        (None, None),
        ([1.0], None),
    ],
)
def test_coerce_float(raw: object, expected: float | None) -> None:
    actual = coerce_float(raw)
    assert actual == expected
    if expected is not None:
        assert isinstance(actual, float)


def test_coerce_bool_and_optional_bool() -> None:
    for truthy in (True, 1, -1, 42, 1.0, 0.5, "1", "true", "TRUE", "t", "yes", "y", "on", " true "):
        assert coerce_bool(truthy) is True
    for falsy in (False, 0, 0.0, "0", "false", "FALSE", "f", "no", "n", "off", "maybe", "", None, [], {}):
        assert coerce_bool(falsy) is False

    assert coerce_optional_bool(True) is True
    assert coerce_optional_bool(False) is False
    assert coerce_optional_bool("yes") is True
    assert coerce_optional_bool("off") is False
    for unknown in (None, "maybe", object()):
        assert coerce_optional_bool(unknown) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("hello", "hello"),
        ("  padded  ", "padded"),
        ("", None),
        (" \t\n ", None),
        (42, None),
        (None, None),
        (True, None),
        ([], None),
    ],
)
def test_coerce_str(raw: object, expected: str | None) -> None:
    assert coerce_str(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1000, 1000),
        (0, 0),
        (-1000, -1000),
        (1000.5, 1000),
        ("1000", 1000),
        ("1000.5", 1000),
        (True, None),
        (False, None),
        ("not-a-date", None),
        ("", None),
        ("  ", None),
        (None, None),
        (float("nan"), None),
        (float("inf"), None),
        (float("-inf"), None),
        ("nan", None),
        ("inf", None),
        ("-inf", None),
        ([1000], None),
    ],
)
def test_coerce_timestamp_ms(raw: object, expected: int | None) -> None:
    assert coerce_timestamp_ms(raw) == expected


def test_timestamp_iso_roundtrip_and_bounds() -> None:
    assert coerce_timestamp_ms("2024-01-01T00:00:00Z") == coerce_timestamp_ms("2024-01-01T09:00:00+09:00")
    for valid_ts in (0, 1000.0):
        iso = timestamp_to_iso(valid_ts)
        assert iso is not None and "1970" in iso
    for invalid_ts in (True, False, 10**20, "abc", None, []):
        assert timestamp_to_iso(invalid_ts) is None


def test_coerce_game_result_lenient_and_strict() -> None:
    assert coerce_game_result(GameResult.BLACK_WIN) is GameResult.BLACK_WIN
    assert isinstance(coerce_game_result(1), GameResult)
    assert isinstance(coerce_game_result(" 1 "), GameResult)
    assert coerce_game_result(" black_win ") == GameResult.BLACK_WIN
    assert coerce_game_result("draw_by_repetition", is_strict=True) == GameResult.DRAW_BY_REPETITION

    for invalid in (None, True, False, 1.0, "abc", "DRAW", "", "   ", 999999, [], {}):
        assert coerce_game_result(invalid) is None
        with pytest.raises(ValueError):
            coerce_game_result(invalid, is_strict=True)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ("hello", ["hello"]),
        ("  hello  ", ["hello"]),
        ("", []),
        (" \t\n ", []),
        (["a", "b", "c"], ["a", "b", "c"]),
        (["  a  ", "", "  ", " b "], ["a", "b"]),
        (("  a  ", "", "b"), ["a", "b"]),
        ([], []),
        ((), []),
    ],
)
def test_coerce_str_list_valid(raw: object, expected: list[str]) -> None:
    assert coerce_str_list(raw) == expected


@pytest.mark.parametrize(
    ("raw", "field", "match"),
    [
        (["a", 123], "paths", "paths.*entries must be strings"),
        (["a", None], "items", "entries must be strings"),
        (42, "overlays", "overlays.*must be a string or list of strings"),
        ({"a": 1}, "items", "must be a string or list of strings"),
        (True, "items", "must be a string or list of strings"),
    ],
)
def test_coerce_str_list_invalid_raises(raw: object, field: str, match: str) -> None:
    with pytest.raises(TypeError, match=match):
        coerce_str_list(raw, field=field)
