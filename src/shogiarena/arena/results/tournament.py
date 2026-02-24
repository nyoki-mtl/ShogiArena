from __future__ import annotations

from dataclasses import dataclass

from shogiarena.arena.configs.tournament import TournamentResults
from shogiarena.arena.results.base import RunResultBase
from shogiarena.arena.services.statistics.sprt import SprtResult
from shogiarena.utils.serialization import json_serialize


@dataclass(slots=True, kw_only=True)
class TournamentRunResult(RunResultBase):
    """トーナメントランナー用の結果構造。"""

    tournament: TournamentResults
    sprt: SprtResult | None = None

    def serialize_extras(self) -> dict[str, object]:
        return {
            "tournament": json_serialize(self.tournament),
            "sprt": json_serialize(self.sprt),
        }
