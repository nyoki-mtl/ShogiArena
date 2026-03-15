"""Capability-separated record codec registry.

Three distinct capabilities:
- RecordSerializer: GameRecord -> bytes | str
- RecordReader: bytes | str -> GameRecord
- PositionStreamExporter: GameRecord -> Iterator[bytes]

Public surface is module-level register/get functions only.
No introspection API is exposed; consumers look up codecs by format_id.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import rshogi

SerializeFn = Callable[[rshogi.record.GameRecord], bytes | str]
DeserializeFn = Callable[[bytes | str], rshogi.record.GameRecord]
StreamExportFn = Callable[[rshogi.record.GameRecord], Iterator[bytes]]


@dataclass(frozen=True, slots=True)
class RecordSerializer:
    """Serialize a GameRecord to a single payload."""

    format_id: str
    serialize: SerializeFn


@dataclass(frozen=True, slots=True)
class RecordReader:
    """Deserialize a payload into a GameRecord."""

    format_id: str
    deserialize: DeserializeFn


@dataclass(frozen=True, slots=True)
class PositionStreamExporter:
    """Export a GameRecord as a stream of position entries."""

    format_id: str
    export: StreamExportFn


# ── module-level registry state ─────────────────────────────────────────

_serializers: dict[str, RecordSerializer] = {}
_readers: dict[str, RecordReader] = {}
_stream_exporters: dict[str, PositionStreamExporter] = {}


def register_serializer(s: RecordSerializer) -> RecordSerializer:
    """Register a serializer for the given format_id."""
    _serializers[s.format_id] = s
    return s


def register_reader(r: RecordReader) -> RecordReader:
    """Register a reader for the given format_id."""
    _readers[r.format_id] = r
    return r


def register_stream_exporter(e: PositionStreamExporter) -> PositionStreamExporter:
    """Register a stream exporter for the given format_id."""
    _stream_exporters[e.format_id] = e
    return e


def get_serializer(format_id: str) -> RecordSerializer | None:
    """Look up a serializer by format_id."""
    return _serializers.get(format_id)


def get_reader(format_id: str) -> RecordReader | None:
    """Look up a reader by format_id."""
    return _readers.get(format_id)


def get_stream_exporter(format_id: str) -> PositionStreamExporter | None:
    """Look up a stream exporter by format_id."""
    return _stream_exporters.get(format_id)


__all__ = [
    "PositionStreamExporter",
    "RecordReader",
    "RecordSerializer",
    "get_reader",
    "get_serializer",
    "get_stream_exporter",
    "register_reader",
    "register_serializer",
    "register_stream_exporter",
]
