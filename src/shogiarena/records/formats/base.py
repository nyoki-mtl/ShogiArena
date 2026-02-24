from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import rshogi

SerializeFn = Callable[[rshogi.record.GameRecord], bytes | str]
DeserializeFn = Callable[[bytes | str], rshogi.record.GameRecord]


@dataclass(frozen=True, slots=True)
class RecordCodec:
    """棋譜フォーマットのエンコード/デコード設定。"""

    format_id: str
    serialize: SerializeFn
    deserialize: DeserializeFn
    supports_partial: bool = False
    media_types: tuple[str, ...] = ("text/plain",)
