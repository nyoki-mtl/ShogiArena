"""Project folded CSA state onto the payloads the live dashboard already speaks.

The live card's ``WorkerSnapshotRecord`` and the CSA fold are nearly a bijection,
so nothing here invents a representation: it renames. Everything CSA-specific that
the card has no slot for goes under ``meta.csa``, which the live stream already
carries end to end as free-form state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from shogiarena._core.contexts.csa_watch.application.run_watcher import RunView
from shogiarena._core.contexts.csa_watch.domain.game_identity import csa_game_key
from shogiarena._core.contexts.csa_watch.domain.result_mapping import arena_game_result
from shogiarena._core.contexts.csa_watch.domain.run_state import HIRATE_SFEN, GameState, RunState
from shogiarena._core.contexts.csa_watch.ports.replay_ports import ReplayResult
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue

MAX_PUBLISHED_ALERTS = 20


def _persistence_status(
    snapshot: Mapping[str, JsonValue] | None,
    section: str,
    identity: str,
    *,
    pending: bool = False,
) -> JsonValue:
    entries = snapshot.get(section) if snapshot is not None else None
    if isinstance(entries, Mapping):
        status = entries.get(identity)
        if isinstance(status, Mapping):
            return dict(status)
    return {"state": "pending", "attempts": 0, "detail": None} if pending else None


def _time_control_spec(game: GameState) -> str | None:
    """Render ``game_start.time`` in the arena's time-control dialect.

    The card derives its clocks by replaying ``move_times_ms`` against this
    string, and `parseTimeControlSpec` only understands ``t<ms>`` with an
    optional ``+i<ms>`` increment or ``+b<ms>`` byoyomi — milliseconds, and the
    leading ``t`` is what selects the "time" mode at all. Writing seconds with no
    prefix parses as nothing and every clock on the card reads 0:00.

    Increment and byoyomi are exclusive here because the tail is one or the
    other; the bridge already picks exclusively for USI, so this matches.
    """
    spec = game.time
    if spec is None:
        return None
    total_ms = spec.total_ms or 0
    byoyomi_ms = spec.byoyomi_ms or 0
    increment_ms = spec.inc_ms or 0
    if not total_ms and not byoyomi_ms and not increment_ms:
        return None
    if byoyomi_ms:
        return f"t{total_ms}+b{byoyomi_ms}"
    if increment_ms:
        return f"t{total_ms}+i{increment_ms}"
    return f"t{total_ms}"


def _per_ply_series(game: GameState, replay: ReplayResult) -> dict[str, list[JsonValue]]:
    """Build the per-ply arrays the card indexes by ``ply - 1``.

    Only our own engine reports an evaluation, so exactly one of the eval arrays
    is ever populated. The other stays null-filled: the opponent is a remote
    black box, not a value that has yet to arrive.
    """
    ply_count = len(game.moves)
    eval_black: list[JsonValue] = [None] * ply_count
    eval_white: list[JsonValue] = [None] * ply_count
    nodes: list[JsonValue] = [None] * ply_count
    depths: list[JsonValue] = [None] * ply_count
    move_times: list[JsonValue] = [None] * ply_count

    for index, move in enumerate(game.moves):
        move_times[index] = move.t_ms
        info = move.eval
        if info is None:
            continue
        nodes[index] = info.nodes
        depths[index] = info.depth
        if info.cp is None:
            continue
        if move.side == "black":
            eval_black[index] = info.cp
        elif move.side == "white":
            eval_white[index] = info.cp

    return {
        "moves": [move.usi for move in game.moves],
        "ki2_moves": list(replay.ki2_moves) if replay.plies else [],
        "eval_black": eval_black,
        "eval_white": eval_white,
        "nodes_values": nodes,
        "depth_values": depths,
        "seldepth_values": [None] * ply_count,
        "move_times_ms": move_times,
        "wall_times_ms": [None] * ply_count,
        "engine_wall_times_ms": [None] * ply_count,
        "latency_deltas_ms": [None] * ply_count,
    }


def build_worker_snapshot(
    view: RunView,
    game: GameState,
    replay: ReplayResult,
    persistence: Mapping[str, JsonValue] | None = None,
) -> JsonObject:
    """Build the live card record for one CSA game."""
    series = _per_ply_series(game, replay)
    game_key = csa_game_key(view.state.run_id, game.game_id)
    snapshot: JsonObject = {
        "game_id": game_key,
        "initial_sfen": game.initial_sfen or "startpos",
        "sfen": replay.final_sfen or game.initial_sfen or "startpos",
        "black_name": game.black_name or "Unknown",
        "white_name": game.white_name or "Unknown",
        "current_ply": game.current_ply,
        "latency_alerts": [False] * game.current_ply,
        "meta": build_csa_meta(view, game, replay, persistence),
    }
    snapshot.update(series)

    result = arena_game_result(game.result, game.my_color, game.terminal)
    if result is not None:
        snapshot["game_result"] = result

    time_control = _time_control_spec(game)
    if time_control is not None:
        snapshot["time_control_black"] = time_control
        snapshot["time_control_white"] = time_control

    if game.black_remaining_ms is not None:
        snapshot["black_remain_ms"] = game.black_remaining_ms
    if game.white_remaining_ms is not None:
        snapshot["white_remain_ms"] = game.white_remaining_ms
    if game.ledger_as_of_ts is not None:
        snapshot["clock_started_at_ms"] = game.ledger_as_of_ts
    if not game.is_finished:
        snapshot["clock_active"] = game.side_to_move
    return snapshot


def build_csa_meta(
    view: RunView,
    game: GameState,
    replay: ReplayResult,
    persistence: Mapping[str, JsonValue] | None = None,
) -> JsonObject:
    """CSA-only state, carried as free-form live-stream metadata.

    Everything here answers "has this gone wrong", which is the reason to watch a
    CSA game at all and which the arena's own view model has no slot for.
    """
    state = view.state
    game_key = csa_game_key(state.run_id, game.game_id)
    pending = game.pending
    score = state.score
    return {
        "csa": {
            "run_id": state.run_id,
            "server_game_id": game.game_id,
            "worker_idx": view.worker_idx,
            "stream_generation": view.stream_generation,
            "current_game_id": state.current_game_id,
            "bridge_version": state.version,
            "phase": state.phase,
            "phase_since_ts": state.phase_since_ts,
            # See `_run_summary_entry` for why liveness travels as three facts
            # rather than one verdict.
            "stopped": state.stopped,
            "emits_liveness": state.emits_liveness,
            "last_event_ts": state.last_ts,
            # The engine's own account of itself, which only `engine_meta` can
            # give: `bridge_version` is the bridge's and says nothing about what
            # actually played the game.
            "engine_name": state.engine_name,
            "engine_author": state.engine_author,
            "engine_options": [{"name": name, "value": value} for name, value in state.engine_options],
            "my_color": game.my_color,
            "persistence": _persistence_status(
                persistence,
                "games",
                game_key,
                pending=game.is_finished,
            ),
            "opponent_name": game.opponent_name,
            "side_to_move": None if game.is_finished else game.side_to_move,
            "alerts": [
                {
                    "seq": alert.seq,
                    "level": alert.level,
                    "code": alert.code,
                    "detail": alert.detail,
                    "ts": alert.ts,
                    "game_id": alert.game_id,
                }
                for alert in state.sorted_alerts()[:MAX_PUBLISHED_ALERTS]
            ],
            "outstanding_search": None
            if pending is None
            else {
                "kind": pending.kind,
                "ply": pending.ply,
                "deadline_ts": pending.deadline_ts,
                "predicted_usi": pending.predicted_usi,
            },
            "deadline_ts": None if pending is None else pending.deadline_ts,
            "last_move_origin": game.moves[-1].by if game.moves else None,
            "fallback_plies": list(game.fallback_plies),
            "ledger": {
                "black_ms": game.black_remaining_ms,
                "white_ms": game.white_remaining_ms,
                "as_of_ts": game.ledger_as_of_ts,
            },
            "score": {"wins": score.wins, "losses": score.losses, "draws": score.draws},
            "games": [
                {
                    "game_id": entry.game_id,
                    "result": entry.result,
                    "current_ply": entry.current_ply,
                    "black_name": entry.black_name,
                    "white_name": entry.white_name,
                    "my_color": entry.my_color,
                    "terminal": list(entry.terminal),
                    # `result` states the outcome from our own side; the arena
                    # states which colour won. The monitoring table renders with
                    # the arena's own result badge, so give it the arena's
                    # vocabulary rather than making the reader re-derive it.
                    "game_result": arena_game_result(entry.result, entry.my_color, entry.terminal),
                    # The fold has held these since the first game_start; they
                    # were simply never published. "Did the 11:30 pairing
                    # actually happen" has no other answer.
                    "started_ts": entry.started_ts,
                    "ended_ts": entry.ended_ts,
                    "persistence": _persistence_status(
                        persistence,
                        "games",
                        csa_game_key(state.run_id, entry.game_id),
                        pending=entry.is_finished,
                    ),
                }
                for entry in state.games
            ],
            "replay": {
                "is_complete": replay.is_complete,
                "failure": None
                if replay.failure is None
                else {
                    "ply": replay.failure.ply,
                    "usi": replay.failure.usi,
                    "reason": replay.failure.reason,
                },
            },
            "log_health": {
                "missing_seq": state.health.missing_seq,
                "malformed": state.health.malformed,
                "invalid_lines": state.health.invalid_lines,
                "orphan_records": state.health.orphan_records,
                "ply_gaps": state.health.ply_gaps,
                "unknown_types": dict(state.health.unknown_types),
                "has_partial_line": view.has_partial_line,
                "restarts": view.stream_generation,
            },
        }
    }


def build_move_progress(game: GameState, ply: int, replay: ReplayResult, *, game_key: str) -> JsonObject:
    """One appended move, in the shape the live stream turns into a moves diff."""
    move = game.moves[ply - 1]
    payload: JsonObject = {
        "type": "move_progress",
        "game_id": game_key,
        "current_ply": ply,
        "move": move.usi,
    }
    if ply <= len(replay.plies):
        entry = replay.plies[ply - 1]
        if entry.ki2:
            payload["ki2_move"] = entry.ki2
        payload["sfen"] = entry.sfen_after
    if move.t_ms is not None:
        payload["time_ms"] = move.t_ms
    info = move.eval
    if info is not None:
        if info.cp is not None:
            payload["eval"] = info.cp
        if info.depth is not None:
            payload["depth"] = info.depth
        if info.nodes is not None:
            payload["nodes"] = info.nodes
    return payload


def build_games_snapshot(views: Sequence[RunView]) -> JsonObject:
    """Project every discovered CSA game onto the shared Games schedule contract."""
    discovered: list[tuple[int, int, RunView, GameState]] = []
    for view in views:
        for index, game in enumerate(view.state.games):
            discovered.append((game.started_ts or 0, index, view, game))

    discovered.sort(key=lambda item: (item[0], item[2].worker_idx, item[1]), reverse=True)
    schedule: list[JsonValue] = []
    for order, (_, _, view, game) in enumerate(discovered, start=1):
        game_key = csa_game_key(view.state.run_id, game.game_id)
        black_owned = game.my_color == "black"
        white_owned = game.my_color == "white"
        schedule.append(
            {
                "game_id": game_key,
                "server_game_id": game.game_id,
                "order": order,
                "display_order": order,
                "status": "completed" if game.is_finished else "running",
                "black": game.black_name,
                "white": game.white_name,
                "black_instance": "Ours" if black_owned else None,
                "white_instance": "Ours" if white_owned else None,
                "black_instance_kind": "csa-owned" if black_owned else None,
                "white_instance_kind": "csa-owned" if white_owned else None,
                "initial_sfen": game.initial_sfen or HIRATE_SFEN,
                "game_result": arena_game_result(game.result, game.my_color, game.terminal),
                "total_plies": game.current_ply,
                "start_time": _iso_timestamp(game.started_ts),
                "end_time": _iso_timestamp(game.ended_ts),
                "worker_idx": view.worker_idx,
                "run_id": view.state.run_id,
            }
        )

    completed = sum(1 for _, _, _, game in discovered if game.is_finished)
    running = len(discovered) - completed
    return {
        "schedule": schedule,
        "total_games": len(schedule),
        "completed_games": completed,
        "running_games": running,
        "pending_games": 0,
        "cancelled_games": 0,
        "session_state": "running" if running else "completed",
        "is_running": running > 0,
    }


def _iso_timestamp(timestamp_ms: int | None) -> str | None:
    if timestamp_ms is None:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).isoformat()


def _run_summary_entry(
    view: RunView,
    persistence: Mapping[str, JsonValue] | None = None,
) -> JsonObject:
    state: RunState = view.state
    score = state.score
    return {
        "run_id": state.run_id,
        "worker_idx": view.worker_idx,
        "stream_generation": view.stream_generation,
        "current_game_id": state.current_game_id,
        "persistence": _persistence_status(persistence, "runs", state.run_id),
        "phase": state.phase,
        "wins": score.wins,
        "losses": score.losses,
        "draws": score.draws,
        "games": len(state.games),
        "alerts": len(state.alerts),
        "bridge_version": state.version,
        # The engine is a property of the run, not of a game, and the unpaired
        # stretch is most of a floodgate hour — so a run waiting for a pairing
        # can still say what it is holding ready.
        "engine_name": state.engine_name,
        "engine_author": state.engine_author,
        "engine_options": [{"name": name, "value": value} for name, value in state.engine_options],
        # A run that has not been paired yet owns no game, so it publishes no
        # worker snapshot and the panel has nothing to read. These two fields let
        # the page draw a waiting run from the summary alone — which is most of a
        # floodgate hour. Additive: `alerts` above stays a count.
        "phase_since_ts": state.phase_since_ts,
        # Liveness, as three honest states rather than a guess:
        #   stopped   — the bridge said it was done
        #   emits     — this bridge writes liveness records, so a silence longer
        #               than the heartbeat interval means it is gone
        #   last_ts   — when anything was last written
        # A reader that sees `emits_liveness` may call a silent run dead. One
        # that does not may only report how long the silence has lasted.
        "stopped": state.stopped,
        "emits_liveness": state.emits_liveness,
        "last_event_ts": state.last_ts,
        "alert_entries": [
            {
                "seq": alert.seq,
                "level": alert.level,
                "code": alert.code,
                "detail": alert.detail,
                "ts": alert.ts,
                "game_id": alert.game_id,
            }
            for alert in state.sorted_alerts()[:MAX_PUBLISHED_ALERTS]
        ],
        "game_entries": [
            {
                "game_id": game.game_id,
                "result": game.result,
                "game_result": arena_game_result(game.result, game.my_color, game.terminal),
                "current_ply": game.current_ply,
                "black_name": game.black_name,
                "white_name": game.white_name,
                "my_color": game.my_color,
                "terminal": list(game.terminal),
                "started_ts": game.started_ts,
                "ended_ts": game.ended_ts,
                "persistence": _persistence_status(
                    persistence,
                    "games",
                    csa_game_key(state.run_id, game.game_id),
                    pending=game.is_finished,
                ),
            }
            for game in state.games
        ],
        "log_health": {
            "missing_seq": state.health.missing_seq,
            "malformed": state.health.malformed,
            "invalid_lines": state.health.invalid_lines,
            "orphan_records": state.health.orphan_records,
            "ply_gaps": state.health.ply_gaps,
            "unknown_types": dict(state.health.unknown_types),
            "has_partial_line": view.has_partial_line,
            "restarts": view.stream_generation,
        },
    }


def build_summary(
    views: Sequence[RunView],
    *,
    timestamp: str,
    run_dir: str | None = None,
    persistence: Mapping[str, JsonValue] | None = None,
) -> JsonObject:
    """A minimal but valid summary.

    The page refuses to boot without one, so this must be answerable before a
    single game exists (``0068`` decision 4).
    """
    total = sum(len(view.state.games) for view in views)
    completed = sum(1 for view in views for game in view.state.games if game.is_finished)
    return {
        "is_summary_ready": True,
        "summary_source": "csa",
        "mode": "csa",
        "tournament_type": "csa",
        "flip_policy": None,
        "num_engines": 0,
        "run_dir": run_dir,
        "leaderboard": [],
        "rating_initial": None,
        "engines": [],
        "engines_meta": [],
        "engine_time_controls": {},
        "default_time_control": None,
        "engine_instances": {},
        "engine_stats": {},
        "pair_results": {},
        "timestamp": timestamp,
        "games": {"completed": completed, "total": total, "cancelled": 0},
        "csa_runs": [_run_summary_entry(view, persistence) for view in views],
        # The page's summary guard rejects any payload without this key and the
        # rejection surfaces as a fatal init error, so omitting it blanks the
        # whole dashboard rather than degrading a single panel.
        "live_view": {
            "version": 1,
            "mode": "csa",
            "progress": {
                "kind": "games",
                "unit_label": "games",
                "completed": completed,
                "total": total,
                "cancelled": 0,
                "is_final": False,
                "state": "normal",
                "updated_at": timestamp,
            },
        },
    }


__all__ = [
    "MAX_PUBLISHED_ALERTS",
    "build_csa_meta",
    "build_games_snapshot",
    "build_move_progress",
    "build_summary",
    "build_worker_snapshot",
]
