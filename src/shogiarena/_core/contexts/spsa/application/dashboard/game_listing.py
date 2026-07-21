"""Build SPSA game list payloads from DB rows and event snapshots."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_float, coerce_game_result, coerce_int
from shogiarena._core.shared.kernel.serialization import json_serialize

GameSnapshotLoader = Callable[[str], JsonObject | None]
VariantResolver = Callable[[JsonValue | None], str]
VariantExtractor = Callable[[str], str | None]


def deduplicate_game_id_entries(game_entries: Sequence[tuple[str, int]]) -> list[tuple[str, int]]:
    """Deduplicate while preserving first occurrence order."""

    seen_ids: set[str] = set()
    ordered_ids: list[tuple[str, int]] = []
    for gid, ts in game_entries:
        if gid in seen_ids:
            continue
        seen_ids.add(gid)
        ordered_ids.append((gid, ts))
    return ordered_ids


def build_games_from_db_rows(
    *,
    rows: Sequence[object],
    game_snapshot_loader: GameSnapshotLoader,
    resolve_variant_id: VariantResolver,
    extract_variant_from_game_id: VariantExtractor,
) -> list[dict[str, object]]:
    """Build game records from DB rows."""

    games: list[dict[str, object]] = []
    for row_obj in rows:
        if not isinstance(row_obj, Sequence) or isinstance(row_obj, str | bytes | bytearray):
            continue
        row_values = tuple(row_obj)
        if len(row_values) < 9:
            continue
        (
            game_name,
            black_player,
            white_player,
            game_result,
            num_moves,
            end_date,
            init_sfen,
            time_control_black,
            time_control_white,
        ) = row_values[:9]
        game_id = str(game_name)
        event_meta = game_snapshot_loader(game_id) or {}
        phase_value = event_meta.get("phase")
        update_idx_value = event_meta.get("update_idx")
        resolved_variant = resolve_variant_id(json_serialize(update_idx_value))
        if update_idx_value is None:
            resolved_variant = extract_variant_from_game_id(game_id) or resolved_variant
        end_time_iso: str | None = None
        # end_date は Game.end_date (DateTime 列) 由来だが、row は object 型で来るため
        # cast ではなく isinstance で絞る（実行時にも保証される）。
        if isinstance(end_date, datetime):
            end_time_iso = end_date.isoformat()
        games.append(
            {
                "game_id": game_id,
                "black_player": black_player if isinstance(black_player, str) else None,
                "white_player": white_player if isinstance(white_player, str) else None,
                "game_result": str(game_result) if isinstance(game_result, str) else None,
                "total_plies": coerce_int(num_moves),
                "end_time": end_time_iso,
                "initial_sfen": init_sfen if isinstance(init_sfen, str) else None,
                "time_control_black": time_control_black if isinstance(time_control_black, str) else None,
                "time_control_white": time_control_white if isinstance(time_control_white, str) else None,
                "variant_id": resolved_variant,
                "phase": phase_value if isinstance(phase_value, str) else None,
            }
        )

    return games


def build_games_from_event_entries(
    *,
    ordered_ids: Sequence[tuple[str, int]],
    search_query: str,
    game_snapshot_loader: GameSnapshotLoader,
    resolve_variant_id: VariantResolver,
    extract_variant_from_game_id: VariantExtractor,
) -> list[dict[str, object]]:
    """Build game records from event-derived IDs and snapshots."""

    query_lower = search_query.lower() if search_query else ""
    records: list[dict[str, object]] = []
    for gid, ts in ordered_ids:
        entry = game_snapshot_loader(gid)
        if entry is None:
            if query_lower and query_lower not in gid.lower():
                continue
            records.append({"game_id": gid, "timestamp": ts, "variant_id": extract_variant_from_game_id(gid)})
            continue

        if query_lower:
            black_player = entry.get("black_player")
            white_player = entry.get("white_player")
            candidates = [
                gid.lower(),
                black_player.lower() if isinstance(black_player, str) else "",
                white_player.lower() if isinstance(white_player, str) else "",
            ]
            if not any(query_lower in value for value in candidates if value):
                continue

        end_time = entry.get("end_time")
        if isinstance(end_time, int | float):
            end_time_value = coerce_float(end_time)
            if end_time_value is not None:
                end_time = datetime.fromtimestamp(end_time_value / 1000.0, tz=UTC).isoformat()

        update_idx_value = entry.get("update_idx")
        resolved_variant = resolve_variant_id(json_serialize(update_idx_value))
        if update_idx_value is None:
            resolved_variant = extract_variant_from_game_id(gid) or resolved_variant

        black_player = entry.get("black_player")
        white_player = entry.get("white_player")
        game_result_raw = entry.get("game_result")
        winner_raw = entry.get("winner")
        num_moves_raw = entry.get("num_moves")
        initial_sfen_raw = entry.get("initial_sfen")
        tc_black_raw = entry.get("time_control_black")
        tc_white_raw = entry.get("time_control_white")
        phase_raw = entry.get("phase")

        game_result = coerce_game_result(game_result_raw)
        if game_result is None and winner_raw is not None:
            winner_idx = coerce_int(winner_raw)
            if winner_idx == 1:
                game_result = GameResult.BLACK_WIN
            elif winner_idx == 0:
                game_result = GameResult.WHITE_WIN
            elif winner_idx is not None:
                game_result = GameResult.DRAW_BY_REPETITION
        records.append(
            {
                "game_id": gid,
                "black_player": black_player if isinstance(black_player, str) else None,
                "white_player": white_player if isinstance(white_player, str) else None,
                "game_result": game_result.name if game_result is not None else None,
                "total_plies": coerce_int(num_moves_raw),
                "end_time": end_time if isinstance(end_time, str) else None,
                "initial_sfen": initial_sfen_raw if isinstance(initial_sfen_raw, str) else None,
                "time_control_black": tc_black_raw if isinstance(tc_black_raw, str) else None,
                "time_control_white": tc_white_raw if isinstance(tc_white_raw, str) else None,
                "variant_id": resolved_variant,
                "phase": phase_raw if isinstance(phase_raw, str) else None,
                "timestamp": ts,
            }
        )

    return records


__all__ = [
    "build_games_from_db_rows",
    "build_games_from_event_entries",
    "deduplicate_game_id_entries",
]
