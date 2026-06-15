"""Record format implementations registered by capability."""

from __future__ import annotations

from collections.abc import Iterator

import rshogi

from shogiarena._core.platform.records.codec_registry import (
    PositionStreamExporter,
    RecordReader,
    RecordSerializer,
    get_reader,
    get_serializer,
    get_stream_exporter,
    register_reader,
    register_serializer,
    register_stream_exporter,
)

# ── CSA ──────────────────────────────────────────────────────────────────


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


register_serializer(
    RecordSerializer(
        format_id="csa",
        serialize=_serialize_csa,
    )
)
register_reader(
    RecordReader(
        format_id="csa",
        deserialize=_deserialize_csa,
    )
)


# ── KIF ──────────────────────────────────────────────────────────────────


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


register_serializer(
    RecordSerializer(
        format_id="kif",
        serialize=_serialize_kif,
    )
)
register_reader(
    RecordReader(
        format_id="kif",
        deserialize=_deserialize_kif,
    )
)


# ── PSV (stream export only) ────────────────────────────────────────────


def _require_move_evals(record: rshogi.record.GameRecord, *, format_label: str) -> None:
    """Validate that every move carries an evaluation (required by psv/sbinpack)."""
    payload = record.to_dict()
    raw_moves = payload.get("moves")
    if not isinstance(raw_moves, list):
        raise ValueError(f"{format_label} serialization requires moves payload")
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
        raise ValueError(f"{format_label} serialization requires eval on move index {missing_eval_index}")


def iter_psv_entries(record: rshogi.record.GameRecord) -> Iterator[bytes]:
    """Return PSV entry iterator generated from GameRecord."""
    _require_move_evals(record, format_label="psv")
    try:
        return iter(record.to_psv())
    except ValueError as exc:
        raise ValueError(f"psv serialization failed: {exc}") from exc


register_stream_exporter(
    PositionStreamExporter(
        format_id="psv",
        export=iter_psv_entries,
    )
)


# ── sbinpack ─────────────────────────────────────────────────────────────


def _serialize_sbinpack(record: rshogi.record.GameRecord) -> bytes:
    _require_move_evals(record, format_label="sbinpack")
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


register_serializer(
    RecordSerializer(
        format_id="sbinpack",
        serialize=_serialize_sbinpack,
    )
)
register_reader(
    RecordReader(
        format_id="sbinpack",
        deserialize=_deserialize_sbinpack,
    )
)


__all__ = [
    "get_reader",
    "get_serializer",
    "get_stream_exporter",
    "iter_psv_entries",
]
