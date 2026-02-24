from __future__ import annotations

import rshogi

from shogiarena.records.formats.base import RecordCodec
from shogiarena.records.formats.registry import register_codec


def _serialize_sbinpack(record: rshogi.record.GameRecord) -> bytes:
    payload = record.to_dict()
    raw_moves = payload.get("moves")
    if not isinstance(raw_moves, list):
        raise ValueError("sbinpack serialization requires moves payload")
    missing_eval_index = next(
        (
            idx
            for idx, move in enumerate(raw_moves)
            if not isinstance(move, dict)
            or not isinstance(move.get("engine_info"), dict)
            or move.get("engine_info", {}).get("eval") is None
        ),
        None,
    )
    if missing_eval_index is not None:
        raise ValueError(f"sbinpack serialization requires eval on move index {missing_eval_index}")

    try:
        return bytes(record.to_sbinpack(stem_score=0, include_main=True, include_variations=False))
    except ValueError as exc:
        raise ValueError(f"sbinpack serialization failed: {exc}") from exc


def _deserialize_sbinpack(payload: bytes | str) -> rshogi.record.GameRecord:
    if isinstance(payload, str):
        raise TypeError("sbinpack deserialize expects bytes payload")

    try:
        return rshogi.record.GameRecord.from_sbinpack(payload)
    except ValueError as exc:
        raise ValueError(f"sbinpack deserialization failed: {exc}") from exc


SBINPACK_CODEC = register_codec(
    RecordCodec(
        format_id="sbinpack",
        supports_partial=True,
        media_types=("application/octet-stream",),
        serialize=_serialize_sbinpack,
        deserialize=_deserialize_sbinpack,
    )
)

__all__ = ["SBINPACK_CODEC"]
