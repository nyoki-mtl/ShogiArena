"""Build final BTD summary sections from results and BTD estimate."""

from __future__ import annotations

from dataclasses import dataclass

from shogiarena._core.contexts.game_session.domain.summary_models import TournamentResults
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.statistics.btd_rating import BTDEstimate


@dataclass(slots=True)
class TournamentSummaryFinalBtdSectionsResponse:
    """Final BTD summary sections used for artifact payload assembly."""

    ratings: JsonObject
    pairs: JsonObject
    engines_meta: list[JsonObject]


class TournamentSummaryFinalBtdSectionsService:
    """Assemble final BTD summary sections."""

    @staticmethod
    def build(
        *,
        results: TournamentResults,
        btd: BTDEstimate,
    ) -> TournamentSummaryFinalBtdSectionsResponse:
        ratings: JsonObject = {
            name: {"elo": float(btd.ratings[name]), "se": float(btd.rating_se.get(name, 0.0))}
            for name in btd.ratings.keys()
        }
        pairs: JsonObject = {
            f"{engine_a}_vs_{engine_b}": {
                "delta_elo": float(pair.delta_elo),
                "standard_error": float(pair.standard_error or 0.0),
                "likelihood_of_superiority": float(pair.likelihood_of_superiority or 0.0),
                "wdl": {
                    engine_a: int(pair_result.get(f"{engine_a}_wins", 0)),
                    engine_b: int(pair_result.get(f"{engine_b}_wins", 0)),
                    "draws": int(pair_result.get("draws", 0)),
                },
            }
            for (engine_a, engine_b), pair_result in results.pair_results.items()
            for pair in (btd.pair_delta(engine_a, engine_b, cov=btd.rating_cov),)
        }
        engines_meta: list[JsonObject] = [
            {"name": name, "elo": float(btd.ratings[name])} for name in btd.ratings.keys()
        ]
        return TournamentSummaryFinalBtdSectionsResponse(
            ratings=ratings,
            pairs=pairs,
            engines_meta=engines_meta,
        )


__all__ = ["TournamentSummaryFinalBtdSectionsService"]
