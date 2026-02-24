from __future__ import annotations

from dataclasses import dataclass

from shogiarena.arena.results.base import RunResultBase
from shogiarena.arena.services.statistics.sprt import SprtResult
from shogiarena.utils.serialization import json_serialize


@dataclass(slots=True, kw_only=True)
class SprtRunResult(RunResultBase):
    """SPRT専用ランナー用の結果構造。"""

    sprt: SprtResult

    def serialize_extras(self) -> dict[str, object]:
        return {"sprt": json_serialize(self.sprt)}
