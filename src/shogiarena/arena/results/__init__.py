from __future__ import annotations

from shogiarena.arena.results.base import RunResultBase
from shogiarena.arena.results.sprt import SprtRunResult
from shogiarena.arena.results.spsa import SpsaRunResult
from shogiarena.arena.results.tournament import TournamentRunResult

__all__ = [
    "RunResultBase",
    "TournamentRunResult",
    "SpsaRunResult",
    "SprtRunResult",
]
