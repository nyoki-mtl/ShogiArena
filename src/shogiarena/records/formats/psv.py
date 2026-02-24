"""PSV (PackedSfenValue) フォーマットの codec。

rshogi の ``GameRecord.to_psv()`` に委譲し、各手ごとに 40 バイトの
バイナリエントリを生成する。
"""

from __future__ import annotations

from collections.abc import Iterator

import rshogi

from shogiarena.records.formats.base import RecordCodec
from shogiarena.records.formats.registry import register_codec


def _deserialize_psv(_payload: bytes | str) -> rshogi.record.GameRecord:
    raise NotImplementedError("psv はバイナリ追記前提のため deserialize は未対応です")


def iter_psv_entries(record: rshogi.record.GameRecord) -> Iterator[bytes]:
    """GameRecord から PSV エントリを返すイテレータを生成する。

    各エントリは 40 バイト（PackedSfen 32B + move/score/ply/result 8B）。
    全手に eval が必要。欠損時は ``ValueError`` を送出する。

    Note:
        内部で ``to_psv()`` がリスト全体をメモリ上に生成するため、
        真の遅延イテレーションではない。
    """
    return iter(record.to_psv())


def _serialize_to_binary(record: rshogi.record.GameRecord) -> bytes:
    return b"".join(iter_psv_entries(record))


PSV_CODEC = register_codec(
    RecordCodec(
        format_id="psv",
        supports_partial=True,
        media_types=("application/octet-stream",),
        serialize=_serialize_to_binary,
        deserialize=_deserialize_psv,
    )
)

__all__ = ["PSV_CODEC", "iter_psv_entries"]
