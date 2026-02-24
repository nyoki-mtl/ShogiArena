from __future__ import annotations

import rshogi

from shogiarena.records.formats.base import RecordCodec
from shogiarena.records.formats.registry import register_codec


def _serialize_csa(record: rshogi.record.GameRecord) -> str:
    return record.to_csa()


def _deserialize_csa(payload: bytes | str) -> rshogi.record.GameRecord:
    if isinstance(payload, str):
        return rshogi.record.GameRecord.from_csa_str(payload)
    encoding = "cp932" if b"SHIFT_JIS" in payload or b"SHIFT-JIS" in payload else "shift_jis"
    try:
        text = payload.decode(encoding)
    except UnicodeDecodeError:
        text = payload.decode("cp932")
    return rshogi.record.GameRecord.from_csa_str(text)


CSA_CODEC = register_codec(
    RecordCodec(
        format_id="csa",
        supports_partial=False,
        media_types=("application/vnd.csa", "text/plain"),
        serialize=_serialize_csa,
        deserialize=_deserialize_csa,
    )
)

__all__ = ["CSA_CODEC"]
