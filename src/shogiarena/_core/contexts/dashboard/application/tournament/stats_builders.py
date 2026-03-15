"""Tournament stats payload builders (head-to-head, pair stats)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from shogiarena._core.contexts.dashboard.application.tournament.payload_signatures import (
    current_timestamp_iso,
    hash_games_payload,
)
from shogiarena._core.contexts.dashboard.application.tournament.payloads import (
    HeadToHeadPayload,
    PairStatsEntry,
    PairStatsPayload,
)
from shogiarena._core.shared.kernel.game_record_types import GameRecordEnginesDict
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_game_result, coerce_int

from .summary_builders import compute_pair_los


def _build_head_to_head_entries(games: Sequence[GameRecordEnginesDict]) -> list[JsonObject]:
    def counter(record: Mapping[str, JsonValue], key: str) -> int:
        return coerce_int(record.get(key)) or 0

    head_to_head: dict[tuple[str, str], JsonObject] = {}
    for game in games:
        black = str(game["black_engine"])
        white = str(game["white_engine"])
        result = game["result"]

        pair = (black, white) if black <= white else (white, black)
        if pair not in head_to_head:
            head_to_head[pair] = {
                "engines": list(pair),
                f"{pair[0]}_wins": 0,
                f"{pair[1]}_wins": 0,
                "draws": 0,
                "total": 0,
            }

        head_to_head[pair]["total"] = counter(head_to_head[pair], "total") + 1

        game_result = coerce_game_result(result, is_strict=True)
        if game_result.is_black_win():
            if black == pair[0]:
                key = f"{pair[0]}_wins"
                head_to_head[pair][key] = counter(head_to_head[pair], key) + 1
            else:
                key = f"{pair[1]}_wins"
                head_to_head[pair][key] = counter(head_to_head[pair], key) + 1
        elif game_result.is_white_win():
            if white == pair[0]:
                key = f"{pair[0]}_wins"
                head_to_head[pair][key] = counter(head_to_head[pair], key) + 1
            else:
                key = f"{pair[1]}_wins"
                head_to_head[pair][key] = counter(head_to_head[pair], key) + 1
        elif game_result.is_draw():
            head_to_head[pair]["draws"] = counter(head_to_head[pair], "draws") + 1
    return list(head_to_head.values())


def build_head_to_head_payload(games: Sequence[GameRecordEnginesDict]) -> HeadToHeadPayload:
    return {
        "head_to_head": _build_head_to_head_entries(games),
        "updated_at": current_timestamp_iso(),
    }


def _build_pair_stats_entries(games: Sequence[GameRecordEnginesDict]) -> list[PairStatsEntry]:
    def counter(record: Mapping[str, JsonValue], key: str) -> int:
        return coerce_int(record.get(key)) or 0

    pair_records: dict[tuple[str, str], JsonObject] = {}
    for game in games:
        black = str(game["black_engine"])
        white = str(game["white_engine"])
        result = game["result"]
        if not black or not white:
            continue
        pair = (black, white) if black <= white else (white, black)
        record = pair_records.setdefault(
            pair,
            {
                "engines": [pair[0], pair[1]],
                "wins": {pair[0]: 0, pair[1]: 0},
                "draws": 0,
                "games": 0,
            },
        )

        record["games"] = counter(record, "games") + 1
        game_result = coerce_game_result(result, is_strict=True)
        if game_result.is_black_win():
            winner = black
        elif game_result.is_white_win():
            winner = white
        elif game_result.is_draw():
            record["draws"] = counter(record, "draws") + 1
            continue
        else:
            continue

        wins_map_raw = record.get("wins")
        if isinstance(wins_map_raw, Mapping):
            wins_map = {str(key): coerce_int(value) or 0 for key, value in wins_map_raw.items()}
        else:
            wins_map = {pair[0]: 0, pair[1]: 0}
        wins_map[winner] = (coerce_int(wins_map.get(winner)) or 0) + 1
        record["wins"] = wins_map

    pairs_payload: list[PairStatsEntry] = []
    for (engine_a, engine_b), record in pair_records.items():
        total = counter(record, "games")
        draws = counter(record, "draws")
        wins_map_raw = record.get("wins")
        wins_map = (
            {str(key): coerce_int(value) or 0 for key, value in wins_map_raw.items()}
            if isinstance(wins_map_raw, Mapping)
            else {}
        )
        wins_a = coerce_int(wins_map.get(engine_a)) or 0
        wins_b = coerce_int(wins_map.get(engine_b)) or 0
        win_rate_a = (wins_a + 0.5 * draws) / total if total > 0 else None
        win_rate_b = (wins_b + 0.5 * draws) / total if total > 0 else None
        los_a = compute_pair_los(wins_a, wins_b, draws)
        los_b = 1 - los_a if los_a is not None else None
        pairs_payload.append(
            {
                "pair_id": f"{engine_a}__vs__{engine_b}",
                "engines": [engine_a, engine_b],
                "games": total,
                "wins": {engine_a: wins_a, engine_b: wins_b},
                "draws": draws,
                "win_rate": {engine_a: win_rate_a, engine_b: win_rate_b},
                "los": {engine_a: los_a, engine_b: los_b},
            }
        )

    pairs_payload.sort(key=lambda entry: (-int(entry["games"]), entry["pair_id"]))
    return pairs_payload


def build_pair_stats_payload(games: Sequence[GameRecordEnginesDict]) -> PairStatsPayload:
    pairs = _build_pair_stats_entries(games)
    return {
        "pairs": pairs,
        "total_pairs": len(pairs),
        "signature": hash_games_payload(pairs),
        "source": "db",
        "fetched_at": current_timestamp_iso(),
    }


__all__ = ["build_head_to_head_payload", "build_pair_stats_payload"]
