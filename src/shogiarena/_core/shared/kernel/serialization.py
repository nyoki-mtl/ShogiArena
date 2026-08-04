"""JSON-safe recursive serialization helpers for governed layers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from shogiarena._core.shared.kernel.json_types import JsonValue


def json_serialize(value: object) -> JsonValue:
    """Recursively convert complex values into JSON-serializable primitives.

    先頭の分岐は、既に JSON プリミティブである葉と中間ノードを
    構造的 match に入る前に落とすためのものである。

    **厳密な型一致**であることが要点で、`isinstance` ではない。したがって
    `IntEnum` / `StrEnum` / `str` のサブクラス / dict を継承した dataclass などは
    ここに一致せず、これまでどおり下の match へ落ちる。
    「`type(x)` が `str` なら、x は BaseModel でも dataclass インスタンスでも Enum でも
    Path でも Mapping でも set でもありえない」——つまり従来も scalar のアームに
    落ちて自身を返していた値だけを先取りしており、**挙動は不変**である。

    この不変性は性能以上に重要である。`json_serialize` は
    `run_artifact_hashes._mapping_to_json_object` 経由で
    `config_fingerprint` / `schedule_hash` / `provenance_hash` に届いており、
    出力型が変われば resume hash の入力が変わる。
    `tests/unit/test_json_serialize.py` が最適化前の実装を oracle として突き合わせる。
    """

    if type(value) is str or type(value) is int or type(value) is float or type(value) is bool:
        return value
    if type(value) is dict:
        return {str(key): json_serialize(item) for key, item in value.items()}
    if type(value) is list:
        return [json_serialize(item) for item in value]

    match value:
        case None:
            return None
        case BaseModel() as model:
            return json_serialize(model.model_dump())
        case dc if is_dataclass(dc) and not isinstance(dc, type):
            return json_serialize(asdict(dc))
        case Enum() as enum_value:
            return enum_value.value
        case Path() as path_value:
            return str(path_value)
        case Mapping() as mapping_value:
            return {str(key): json_serialize(item) for key, item in mapping_value.items()}
        case set() as set_value:
            return [json_serialize(item) for item in sorted(set_value, key=repr)]
        case str() | int() | float() | bool() as scalar:
            return scalar
        case bytes() | bytearray():
            return str(value)
        case Sequence() as sequence_value:
            return [json_serialize(item) for item in sequence_value]
        case _:
            # Best-effort stringify for governed display/snapshot/log payloads: this serializer
            # is intentionally lenient so an unexpected value never aborts a snapshot or log line.
            # Hash inputs must NOT rely on this — `normalize_for_hash` fails fast on unknown types
            # to keep hashes deterministic.
            return str(value)


__all__ = ["json_serialize"]
