from __future__ import annotations

from collections.abc import Iterable

from shogiarena.records.formats.base import RecordCodec


class RecordCodecRegistry:
    """利用可能なRecordCodecを収集・検索するレジストリ。"""

    def __init__(self) -> None:
        self._codecs: dict[str, RecordCodec] = {}

    def register(self, codec: RecordCodec) -> RecordCodec:
        self._codecs[codec.format_id] = codec
        return codec

    def get(self, format_id: str) -> RecordCodec | None:
        return self._codecs.get(format_id)

    def supported_formats(self) -> Iterable[str]:
        return tuple(self._codecs.keys())


registry = RecordCodecRegistry()


def register_codec(codec: RecordCodec) -> RecordCodec:
    return registry.register(codec)


def get_codec(format_id: str) -> RecordCodec | None:
    return registry.get(format_id)


def supported_formats() -> Iterable[str]:
    return registry.supported_formats()
