"""Build dashboard-specific summary sections from runtime/results."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from shogiarena._core.contexts.game_session.application.summary.runtime_context import TournamentSummaryRuntimeContext
from shogiarena._core.contexts.game_session.domain.summary_models import TournamentResults
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.service_ports import GameRecordPlayers
from shogiarena._core.shared.kernel.statistics.btd_rating import BTDEstimator
from shogiarena._core.shared.kernel.statistics.pentanomial import compute_pentanomial


@dataclass(slots=True)
class TournamentSummaryDashboardSectionsResponse:
    """Dashboard summary sections derived from runtime and current games."""

    btd: JsonObject
    records_summary: JsonObject | None = None
    pentanomial: JsonValue | None = None
    sprt: JsonObject | None = None
    pair_outcome: dict[str, int] | None = None


class TournamentSummaryDashboardSectionsService:
    """Assemble dashboard metric sections used by summary payload updates."""

    @staticmethod
    def build(
        runtime: TournamentSummaryRuntimeContext,
        *,
        results: TournamentResults,
        games: Sequence[GameRecordPlayers],
    ) -> TournamentSummaryDashboardSectionsResponse:
        engine_names = [str(e.name) for e in runtime.request.config.engines]
        anchor_name = engine_names[0] if engine_names else None
        btd = BTDEstimator().estimate(games, anchor_name=anchor_name, engine_names=engine_names)

        cov_map: dict[str, dict[str, float]] = {}
        rating_cov = btd.rating_cov
        if rating_cov is not None:
            for (left, right), value in rating_cov.items():
                cov_map.setdefault(left, {})[right] = float(value)

        btd_section: JsonObject = {
            "ratings": {
                name: {
                    "elo": float(btd.ratings[name]),
                    "se": float(se) if (se := btd.rating_se.get(name)) is not None else None,
                }
                for name in btd.ratings.keys()
            },
            "anchor": btd.anchor,
            "gamma_elo": float(btd.gamma_elo),
            "gamma_elo_se": float(btd.gamma_elo_se) if btd.gamma_elo_se is not None else None,
            "draw_eq": float(btd.draw_eq),
            "draw_eq_se": float(btd.draw_eq_se) if btd.draw_eq_se is not None else None,
            "rating_cov": cov_map,
        }

        record_writer = runtime.dependencies.record_writer
        records_summary: JsonObject | None = None
        if record_writer is not None:
            records_summary = dict(record_writer.get_records_summary())
        pentanomial = json_serialize(compute_pentanomial(games)) if games else None

        sprt: JsonObject | None = None
        sprt_service = runtime.dependencies.sprt_service
        if sprt_service is not None:
            sprt_status = sprt_service.get_status()
            sprt_payload = runtime.actions.build_sprt_payload()
            sprt_payload.update(
                {
                    "llr": sprt_status.llr,
                    "lower": sprt_status.lower_bound,
                    "upper": sprt_status.upper_bound,
                    "decision": sprt_status.decision.value,
                    "games": sprt_status.games_played,
                    "wins": sprt_status.wins,
                    "draws": sprt_status.draws,
                    "losses": sprt_status.losses,
                    "elo_estimate": sprt_status.elo_estimate,
                }
            )
            sprt = sprt_payload

        pair_outcome: dict[str, int] | None = None
        if len(runtime.request.config.engines) == 2:
            first_engine = str(runtime.request.config.engines[0].name)
            second_engine = str(runtime.request.config.engines[1].name)
            pair_key = (first_engine, second_engine) if first_engine <= second_engine else (second_engine, first_engine)
            pair = results.pair_results.get(pair_key)
            if pair is not None:
                if first_engine == pair_key[0]:
                    wins_a = int(pair.get(f"{pair_key[0]}_wins", 0))
                    wins_b = int(pair.get(f"{pair_key[1]}_wins", 0))
                else:
                    wins_a = int(pair.get(f"{pair_key[1]}_wins", 0))
                    wins_b = int(pair.get(f"{pair_key[0]}_wins", 0))
                pair_outcome = {
                    "wins_a": wins_a,
                    "wins_b": wins_b,
                    "draws": int(pair.get("draws", 0)),
                }

        return TournamentSummaryDashboardSectionsResponse(
            btd=btd_section,
            records_summary=records_summary,
            pentanomial=pentanomial,
            sprt=sprt,
            pair_outcome=pair_outcome,
        )


__all__ = ["TournamentSummaryDashboardSectionsResponse", "TournamentSummaryDashboardSectionsService"]
