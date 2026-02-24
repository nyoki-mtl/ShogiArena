from __future__ import annotations

import rshogi

from shogiarena.records.formats.base import RecordCodec
from shogiarena.records.formats.registry import register_codec


def _serialize_kif(record: rshogi.record.GameRecord) -> str:
    return record.to_kif()


def _deserialize_kif(payload: bytes | str) -> rshogi.record.GameRecord:
    if isinstance(payload, str):
        return rshogi.record.GameRecord.from_kif_str(payload)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        text = payload.decode("cp932")
    return rshogi.record.GameRecord.from_kif_str(text)


KIF_CODEC = register_codec(
    RecordCodec(
        format_id="kif",
        supports_partial=False,
        media_types=("application/x-kif", "text/plain"),
        serialize=_serialize_kif,
        deserialize=_deserialize_kif,
    )
)

__all__ = ["KIF_CODEC"]
