from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from shogiarena.arena.results.base import RunResultBase
from shogiarena.utils.serialization import json_serialize


@dataclass(slots=True, kw_only=True)
class SpsaRunResult(RunResultBase):
    """SPSAランナー用の結果構造。"""

    final_params: Mapping[str, float] = field(default_factory=dict)

    def serialize_extras(self) -> dict[str, object]:
        return {"final_params": json_serialize(self.final_params)}
