"""OpenBench result aggregation helpers."""

from __future__ import annotations

import re

from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str
from shogiarena._core.shared.kernel.service_ports import DatabaseServicePort, GameRecordPlayers

from .client_types import OpenBenchCounters


def _round_index_from_game_name(game_name: str) -> int | None:
    match = re.match(r"^g(?P<round>\d+)-", game_name)
    if match is None:
        return None
    one_based = coerce_int(match.group("round"))
    if one_based is None:
        return None
    if one_based <= 0:
        return None
    return one_based - 1


def _score_from_tested(result: GameResult, *, is_tested_black: bool) -> float:
    if result.is_draw():
        return 0.5
    if is_tested_black:
        if result.is_black_win():
            return 1.0
        if result.is_white_win():
            return 0.0
    else:
        if result.is_white_win():
            return 1.0
        if result.is_black_win():
            return 0.0
    return 0.5


def compute_totals(db: DatabaseServicePort, *, tested_engine: str, base_engine: str) -> OpenBenchCounters:
    games = db.get_games_with_players(game_type="arena")
    relevant: list[GameRecordPlayers] = []
    totals = OpenBenchCounters()
    for game in games:
        black = coerce_str(game.get("black_player")) or ""
        white = coerce_str(game.get("white_player")) or ""
        if {black, white} != {tested_engine, base_engine}:
            continue
        result_raw = game["result"]
        relevant.append(game)
        is_tested_black = black == tested_engine
        tested_score = _score_from_tested(result_raw, is_tested_black=is_tested_black)
        if tested_score >= 1.0:
            totals.wins += 1
        elif tested_score <= 0.0:
            totals.losses += 1
        else:
            totals.draws += 1

        if result_raw in {GameResult.BLACK_WIN_BY_TIMEOUT, GameResult.WHITE_WIN_BY_TIMEOUT}:
            totals.timelosses += 1
        if result_raw in {GameResult.BLACK_WIN_BY_ILLEGAL_MOVE, GameResult.WHITE_WIN_BY_ILLEGAL_MOVE}:
            totals.illegals += 1
        if result_raw in {GameResult.ERROR, GameResult.INVALID}:
            totals.crashes += 1
    # Pentanomial:
    # - Use deterministic pairing via round token in game_name ("g0001-..."), two rounds per opening pair.
    round_groups: dict[tuple[str, int], list[GameRecordPlayers]] = {}
    for game in relevant:
        sfen = coerce_str(game.get("initial_sfen")) or "startpos"
        game_name = coerce_str(game.get("game_name")) or ""
        round_idx = _round_index_from_game_name(game_name)
        if round_idx is None:
            continue
        pair_slot = round_idx // 2
        round_groups.setdefault((sfen, pair_slot), []).append(game)

    def add_pairs(group: list[GameRecordPlayers]) -> None:
        tested_black_games = [g for g in group if (coerce_str(g.get("black_player")) or "") == tested_engine]
        tested_white_games = [g for g in group if (coerce_str(g.get("white_player")) or "") == tested_engine]
        count = min(len(tested_black_games), len(tested_white_games))
        for idx in range(count):
            g1 = tested_black_games[idx]
            g2 = tested_white_games[idx]
            r1 = g1["result"]
            r2 = g2["result"]
            score = _score_from_tested(r1, is_tested_black=True) + _score_from_tested(r2, is_tested_black=False)
            if score >= 1.99:
                totals.ww += 1
            elif score >= 1.49:
                totals.dw += 1
            elif score >= 0.99:
                totals.dd += 1
            elif score >= 0.49:
                totals.ld += 1
            else:
                totals.ll += 1

    for group in round_groups.values():
        add_pairs(group)
    return totals


__all__ = ["compute_totals"]
