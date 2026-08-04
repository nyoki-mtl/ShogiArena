"""`json_serialize` の意味論を固定する。

この関数は 75 ファイル・246 箇所から呼ばれる shared kernel のプリミティブでありながら、
専用のテストが 1 件も無かった。task 0065 で exact-type の fast path を前置したので、
**fast path に落ちる値と落ちない値の境界**をここで表明する。

特に重要なのは、`json_serialize` が
`run_artifact_hashes._mapping_to_json_object` 経由で
`config_fingerprint` / `schedule_hash` / `provenance_hash`（resume 契約）に
届いていることである。出力型が変わると resume hash の入力が変わる。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum, IntEnum, StrEnum
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from shogiarena._core.shared.kernel.serialization import json_serialize


class _Color(Enum):
    RED = "red"


class _Level(IntEnum):
    HIGH = 3


class _Name(StrEnum):
    ALPHA = "alpha"


class _Model(BaseModel):
    count: int
    label: str


@dataclass
class _Point:
    x: int
    y: list[int]


class _StrSubclass(str):
    pass


class _IntSubclass(int):
    pass


class _DictSubclass(dict[str, Any]):
    pass


class _CustomMapping(Mapping[str, Any]):
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Any:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


class _Opaque:
    def __repr__(self) -> str:
        return "<opaque>"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("text", "text"),
        (7, 7),
        (1.5, 1.5),
        (True, True),
        (False, False),
        ({}, {}),
        ([], []),
        ({"a": 1, "b": [2, {"c": "d"}]}, {"a": 1, "b": [2, {"c": "d"}]}),
        ([1, "two", None, [3]], [1, "two", None, [3]]),
    ],
)
def test_json_primitives_pass_through(value: object, expected: object) -> None:
    """既に JSON プリミティブな値は同じ形で返る（fast path の対象）。"""

    assert json_serialize(value) == expected


def test_bool_stays_bool_and_is_not_widened_to_int() -> None:
    """`bool` は `int` のサブクラスなので、型ごと保たれることを明示する。"""

    result = json_serialize(True)
    assert result is True
    assert isinstance(result, bool)


def test_non_string_dict_keys_are_stringified() -> None:
    assert json_serialize({1: "a", None: "b"}) == {"1": "a", "None": "b"}


def test_nan_and_infinity_are_preserved_as_floats() -> None:
    assert math.isnan(float(json_serialize(float("nan"))))  # type: ignore[arg-type]
    assert json_serialize(float("inf")) == float("inf")


# ── fast path に落ちてはいけない値 ────────────────────────────────────────
#
# exact-type の判定なので、以下はすべて構造的 match 側へ落ちる必要がある。
# ここが崩れると挙動が静かに変わる。


def test_enum_returns_its_value() -> None:
    assert json_serialize(_Color.RED) == "red"


def test_int_enum_returns_its_value_not_the_member() -> None:
    """`IntEnum` は `int` のサブクラスなので、fast path に落ちると member が返る。"""

    result = json_serialize(_Level.HIGH)
    assert result == 3
    assert type(result) is int
    assert not isinstance(result, _Level)


def test_str_enum_returns_its_value_not_the_member() -> None:
    """`StrEnum` は `str` のサブクラスなので、fast path に落ちると member が返る。"""

    result = json_serialize(_Name.ALPHA)
    assert result == "alpha"
    assert type(result) is str
    assert not isinstance(result, _Name)


def test_str_and_int_subclasses_are_not_narrowed_by_the_fast_path() -> None:
    """Enum ではないサブクラスは、従来どおり scalar のアームでそのまま返る。"""

    assert json_serialize(_StrSubclass("x")) == "x"
    assert json_serialize(_IntSubclass(9)) == 9


def test_pydantic_model_is_dumped() -> None:
    assert json_serialize(_Model(count=2, label="l")) == {"count": 2, "label": "l"}


def test_dataclass_is_converted_recursively() -> None:
    assert json_serialize(_Point(x=1, y=[2, 3])) == {"x": 1, "y": [2, 3]}


def test_dataclass_type_itself_is_stringified_not_converted() -> None:
    """`is_dataclass` はクラスにも真を返すので、インスタンスだけを対象にしている。"""

    assert isinstance(json_serialize(_Point), str)


def test_path_is_stringified() -> None:
    assert json_serialize(Path("a") / "b") == str(Path("a") / "b")


def test_dict_subclass_and_custom_mapping_go_through_the_mapping_arm() -> None:
    assert json_serialize(_DictSubclass({"k": _Color.RED})) == {"k": "red"}
    assert json_serialize(_CustomMapping({"k": _Color.RED})) == {"k": "red"}


def test_tuple_and_set_become_lists() -> None:
    assert json_serialize((1, 2)) == [1, 2]
    assert json_serialize({"b", "a"}) == ["a", "b"]


def test_bytes_are_stringified() -> None:
    assert json_serialize(b"ab") == str(b"ab")


def test_unknown_objects_fall_back_to_repr_style_stringification() -> None:
    assert json_serialize(_Opaque()) == "<opaque>"


def test_nested_structure_mixes_fast_and_slow_paths() -> None:
    """実データの形（dict と list の中に Enum / model / Path が混ざる）を 1 本で見る。"""

    payload = {
        "scalar": 1,
        "level": _Level.HIGH,
        "items": [_Color.RED, {"path": Path("p"), "model": _Model(count=0, label="")}],
        "nested": {"flag": True, "none": None},
    }
    assert json_serialize(payload) == {
        "scalar": 1,
        "level": 3,
        "items": ["red", {"path": str(Path("p")), "model": {"count": 0, "label": ""}}],
        "nested": {"flag": True, "none": None},
    }


def _pre_0065_json_serialize(value: object) -> Any:
    """task 0065 で fast path を入れる**前**の実装を oracle として凍結したもの。

    fast path は「厳密な型一致なので、従来も scalar / Mapping / Sequence の
    アームに落ちていた値だけを先取りする」という主張の上に立っている。
    その主張を、実装を並べて突き合わせることで表明する。
    ここを変更するときは、それが意図した挙動変更であることを説明できなければならない。
    """

    match value:
        case None:
            return None
        case BaseModel() as model:
            return _pre_0065_json_serialize(model.model_dump())
        case dc if is_dataclass(dc) and not isinstance(dc, type):
            return _pre_0065_json_serialize(asdict(dc))
        case Enum() as enum_value:
            return enum_value.value
        case Path() as path_value:
            return str(path_value)
        case Mapping() as mapping_value:
            return {str(key): _pre_0065_json_serialize(item) for key, item in mapping_value.items()}
        case set() as set_value:
            return [_pre_0065_json_serialize(item) for item in sorted(set_value, key=repr)]
        case str() | int() | float() | bool() as scalar:
            return scalar
        case bytes() | bytearray():
            return str(value)
        case Sequence() as sequence_value:
            return [_pre_0065_json_serialize(item) for item in sequence_value]
        case _:
            return str(value)


_ORACLE_CORPUS: list[object] = [
    None,
    "",
    "text",
    0,
    -1,
    7,
    0.0,
    1.5,
    True,
    False,
    b"ab",
    bytearray(b"cd"),
    {},
    [],
    (),
    {1: "a", None: "b"},
    {"a": 1, "b": [2, {"c": "d"}]},
    [1, "two", None, [3]],
    (1, 2),
    {"b", "a"},
    _Color.RED,
    _Level.HIGH,
    _Name.ALPHA,
    _StrSubclass("x"),
    _IntSubclass(9),
    _Model(count=2, label="l"),
    _Point(x=1, y=[2, 3]),
    _Point,
    Path("a") / "b",
    _DictSubclass({"k": _Color.RED}),
    _CustomMapping({"k": _Color.RED}),
    _Opaque(),
    {"deep": [{"level": _Level.HIGH, "path": Path("p")}, (_Color.RED, _StrSubclass("s"))]},
]


@pytest.mark.parametrize("value", _ORACLE_CORPUS, ids=lambda v: type(v).__name__)
def test_fast_path_matches_the_pre_optimization_implementation(value: object) -> None:
    expected = _pre_0065_json_serialize(value)
    actual = json_serialize(value)
    assert actual == expected
    # 型まで一致していること。`3` と `_Level.HIGH` は `==` では区別できない。
    assert type(actual) is type(expected)


def test_result_is_a_new_container_not_the_input_object() -> None:
    """fast path でも container はコピーされる（呼び出し元が結果を変更しうる）。"""

    source: dict[str, Any] = {"items": [1, 2]}
    result = json_serialize(source)
    assert result == source
    assert result is not source
    assert result["items"] is not source["items"]  # type: ignore[index]
