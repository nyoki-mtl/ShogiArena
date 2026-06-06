"""Result aggregation service for tournament summaries."""

from __future__ import annotations

import logging

from shogiarena._core.contexts.game_session.application.summary.runtime_context import TournamentSummaryRuntimeContext
from shogiarena._core.contexts.game_session.domain.summary_models import EngineWdlCounts, TournamentResults

logger = logging.getLogger(__name__)


class TournamentSummaryResultsService:
    """Aggregate DB game records into tournament result structures."""

    @staticmethod
    def calculate_results(runtime: TournamentSummaryRuntimeContext) -> TournamentResults:
        logger.debug("Calculating tournament results")

        db = runtime.dependencies.db_service
        assert db is not None
        game_type = "generate" if runtime.actions.is_generate_run() else "arena"
        games = db.get_games_with_players(game_type=game_type)

        engine_stats: dict[str, EngineWdlCounts] = {}
        for engine in runtime.request.config.engines:
            entry: EngineWdlCounts = {
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "games": 0,
            }
            engine_stats[str(engine.name)] = entry

        pair_results = {}

        for game in games:
            black = game["black_player"]
            white = game["white_player"]
            result = game["result"]

            engine_stats[black]["games"] += 1
            engine_stats[white]["games"] += 1

            if result.is_black_win():
                engine_stats[black]["wins"] += 1
                engine_stats[white]["losses"] += 1
            elif result.is_white_win():
                engine_stats[white]["wins"] += 1
                engine_stats[black]["losses"] += 1
            elif result.is_draw():
                engine_stats[black]["draws"] += 1
                engine_stats[white]["draws"] += 1

            pair = (black, white) if black <= white else (white, black)
            if pair not in pair_results:
                pair_results[pair] = {
                    f"{pair[0]}_wins": 0,
                    f"{pair[1]}_wins": 0,
                    "draws": 0,
                }

            if result.is_black_win():
                if black == pair[0]:
                    pair_results[pair][f"{pair[0]}_wins"] += 1
                else:
                    pair_results[pair][f"{pair[1]}_wins"] += 1
            elif result.is_white_win():
                if white == pair[0]:
                    pair_results[pair][f"{pair[0]}_wins"] += 1
                else:
                    pair_results[pair][f"{pair[1]}_wins"] += 1
            elif result.is_draw():
                pair_results[pair]["draws"] += 1

        cancelled_count = len(runtime.state.cancelled_game_ids)
        total_games = runtime.state.original_total_games or (len(runtime.state.game_schedule) + cancelled_count)

        return TournamentResults(
            engine_stats=engine_stats,
            pair_results=pair_results,
            completed_games=list(runtime.state.completed_game_ids),
            total_games=total_games,
            completed_games_count=len(runtime.state.completed_game_ids),
            cancelled_games_count=cancelled_count,
        )


__all__ = ["TournamentSummaryResultsService"]
