"""Helpers for attaching engine metric metadata to rsshogi records."""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
from typing import Any

import rsshogi.record


def _ensure_mapping(parent: MutableMapping[Any, Any], key: str) -> MutableMapping[Any, Any]:
    value = parent.get(key)
    created: MutableMapping[Any, Any] = {}
    if isinstance(value, MutableMapping):
        for raw_key, raw_value in value.items():
            created[raw_key] = raw_value
    parent[key] = created
    return created


def attach_engine_wall_times(
    record: rsshogi.record.Record,
    engine_wall_times_ms: Sequence[int | None],
) -> rsshogi.record.Record:
    """Return a record whose move engine extras contain engine wall times.

    rsshogi's ``Record.moves`` and ``MoveEntry.engine_info`` accessors return clone values.
    Mutating those clones does not update the original record, so this helper patches the
    serializable payload and rebuilds the record instead.
    """

    if not any(value is not None for value in engine_wall_times_ms):
        return record

    payload = record.to_dict()
    if not isinstance(payload, MutableMapping):
        raise TypeError("Record.to_dict() must return a mapping")
    moves = payload.get("moves")
    if not isinstance(moves, list):
        raise TypeError("Record payload must include a moves list")

    for move_payload, engine_wall_time_ms in zip(moves, engine_wall_times_ms, strict=False):
        if engine_wall_time_ms is None:
            continue
        if not isinstance(move_payload, MutableMapping):
            raise TypeError("Record move payload must be a mapping")
        engine_info = _ensure_mapping(move_payload, "engine_info")
        extras = _ensure_mapping(engine_info, "extras")
        extras["engine_wall_time_ms"] = int(engine_wall_time_ms)

    return rsshogi.record.Record.from_dict(payload, strict=True)


def attach_move_source_metadata(
    record: rsshogi.record.Record,
    *,
    move_sources: Sequence[str | None] = (),
    book_hits: Sequence[bool | None] = (),
) -> rsshogi.record.Record:
    """Return a record whose move extras contain source/provenance hints.

    ``move_source`` is an explicit scalar label such as ``book``, ``search``, or ``unknown``.
    Additional labels such as ``mate`` may be supplied by external record producers or future
    engine-specific signals. ``book_hit`` is nullable because ShogiArena can only fill it when a
    caller has performed book lookup/provenance analysis.
    """

    if not any(value is not None for value in move_sources) and not any(value is not None for value in book_hits):
        return record

    payload = record.to_dict()
    if not isinstance(payload, MutableMapping):
        raise TypeError("Record.to_dict() must return a mapping")
    moves = payload.get("moves")
    if not isinstance(moves, list):
        raise TypeError("Record payload must include a moves list")

    for index, move_payload in enumerate(moves):
        source = move_sources[index] if index < len(move_sources) else None
        book_hit = book_hits[index] if index < len(book_hits) else None
        if source is None and book_hit is None:
            continue
        if not isinstance(move_payload, MutableMapping):
            raise TypeError("Record move payload must be a mapping")
        engine_info = _ensure_mapping(move_payload, "engine_info")
        extras = _ensure_mapping(engine_info, "extras")
        if source is not None:
            extras["move_source"] = source
        if book_hit is not None:
            extras["book_hit"] = bool(book_hit)

    return rsshogi.record.Record.from_dict(payload, strict=True)


__all__ = ["attach_engine_wall_times", "attach_move_source_metadata"]
