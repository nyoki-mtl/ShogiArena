"""Helpers for attaching engine metric metadata to rshogi records."""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence

import rshogi.record


def _ensure_mapping(parent: MutableMapping[object, object], key: str) -> MutableMapping[object, object]:
    value = parent.get(key)
    created: MutableMapping[object, object] = {}
    if isinstance(value, MutableMapping):
        for raw_key, raw_value in value.items():
            created[raw_key] = raw_value
    parent[key] = created
    return created


def attach_engine_wall_times(
    record: rshogi.record.GameRecord,
    engine_wall_times_ms: Sequence[int | None],
) -> rshogi.record.GameRecord:
    """Return a record whose move engine extras contain engine wall times.

    rshogi's ``GameRecord.moves`` and ``MoveRecord.engine_info`` accessors return clone values.
    Mutating those clones does not update the original record, so this helper patches the
    serializable payload and rebuilds the record instead.
    """

    if not any(value is not None for value in engine_wall_times_ms):
        return record

    payload = record.to_dict()
    if not isinstance(payload, MutableMapping):
        raise TypeError("GameRecord.to_dict() must return a mapping")
    moves = payload.get("moves")
    if not isinstance(moves, list):
        raise TypeError("GameRecord payload must include a moves list")

    for move_payload, engine_wall_time_ms in zip(moves, engine_wall_times_ms, strict=False):
        if engine_wall_time_ms is None:
            continue
        if not isinstance(move_payload, MutableMapping):
            raise TypeError("GameRecord move payload must be a mapping")
        engine_info = _ensure_mapping(move_payload, "engine_info")
        extras = _ensure_mapping(engine_info, "extras")
        extras["engine_wall_time_ms"] = int(engine_wall_time_ms)

    return rshogi.record.GameRecord.from_dict(payload, strict=True)


__all__ = ["attach_engine_wall_times"]
