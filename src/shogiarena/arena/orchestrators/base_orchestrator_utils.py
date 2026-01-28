"""Utility helpers shared by arena orchestrators.

This module hosts stateless helpers that were previously exposed as
``BaseOrchestrator`` staticmethods. Splitting them out keeps the base class
focused on coordinating shared state while still providing a single import
point for tournament/SPSA orchestrators.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import zlib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import cshogi
import yaml

from shogiarena.arena.engines.time_control import TimeControlLimits
from shogiarena.arena.execution.game_runner import GameRunner
from shogiarena.arena.services.game_control.adjudication import AdjudicationConfig
from shogiarena.records import GameInfo
from shogiarena.utils.board import normalize_sfen
from shogiarena.utils.common import project_dirs
from shogiarena.utils.common.constants import MOVE_END
from shogiarena.utils.common.paths import resolve_path_like
from shogiarena.utils.types.types import GameResult

logger = logging.getLogger(__name__)

_OVERLAY_CACHE: dict[Path, dict[str, Any]] = {}


# --- Identifier helpers ---------------------------------------------------


def numeric_game_id(game_id: str | int) -> int:
    """Convert a game identifier to a stable non-negative integer."""
    if isinstance(game_id, int):
        return game_id
    if isinstance(game_id, str) and game_id.startswith("game_"):
        return int(game_id.split("_", 1)[1])
    return zlib.crc32(str(game_id).encode("utf-8")) & 0x7FFFFFFF


# --- Engine option helpers -------------------------------------------------


def _load_overlay_options(path: Path) -> dict[str, Any]:
    cached = _OVERLAY_CACHE.get(path)
    if cached is not None:
        return dict(cached)
    if not path.exists():
        raise FileNotFoundError(f"overlay config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TypeError(f"overlay YAML must be a mapping: {path}")
    overlay_opts = raw.get("options") if "options" in raw else raw
    if not isinstance(overlay_opts, dict):
        raise TypeError(f"overlay options must be a mapping: {path}")
    _OVERLAY_CACHE[path] = dict(overlay_opts)
    return dict(overlay_opts)


def build_usi_options(base_extra: dict[str, Any] | None, engine_spec: Any) -> dict[str, Any] | None:
    """Merge arena-level extra options with engine-specific overlays/options."""
    overlay: dict[str, Any] = {}
    artifact = getattr(engine_spec, "artifact", None)
    if isinstance(artifact, str) and artifact:
        repo_name = artifact.split("/", 1)[0]
        overlay_path = project_dirs.overlays.get(repo_name)
        if overlay_path is not None:
            overlay.update(_load_overlay_options(overlay_path))

    base = base_extra or {}
    overlay_opts = engine_spec.load_overlay_options()
    inline_opts = engine_spec.options if isinstance(engine_spec.options, dict) else {}

    if not overlay and not base and not overlay_opts and not inline_opts:
        return None
    merged: dict[str, Any] = dict(overlay)
    merged.update(base)
    merged.update(overlay_opts)
    merged.update(inline_opts)
    if not merged:
        return None

    for key, value in list(merged.items()):
        if not isinstance(value, str):
            continue
        resolved = resolve_path_like(
            value,
            output_dir=project_dirs.output_dir,
            engine_dir=project_dirs.engine_dir,
        )
        merged[key] = resolved
    return merged


def build_time_control_limits(base_tc: Any | None, override_tc: Any | None) -> TimeControlLimits | None:
    """Combine base and per-engine time control into final limits with validation."""
    if base_tc is None and override_tc is None:
        return None

    def pick_int(obj: Any | None, base: Any | None, name: str) -> int | None:
        if obj is not None:
            value = getattr(obj, name, None)
            if value is not None:
                return int(value)
        if base is not None:
            value = getattr(base, name, None)
            if value is not None:
                return int(value)
        return None

    def pick_bool(obj: Any | None, base: Any | None, name: str, default: bool = False) -> bool:
        if obj is not None and getattr(obj, name, None) is not None:
            return bool(getattr(obj, name))
        if base is not None and getattr(base, name, None) is not None:
            return bool(getattr(base, name))
        return default

    expiry_override = getattr(override_tc, "expiry_margin_ms", None) if override_tc is not None else None
    if expiry_override is not None:
        expiry_margin_ms = int(expiry_override)
    else:
        expiry_base = getattr(base_tc, "expiry_margin_ms", None) if base_tc is not None else None
        expiry_margin_ms = int(expiry_base) if expiry_base is not None else 500

    limits_kwargs: dict[str, Any] = {
        "time_ms": pick_int(override_tc, base_tc, "time_ms"),
        "increment_ms": pick_int(override_tc, base_tc, "increment_ms"),
        "byoyomi_ms": pick_int(override_tc, base_tc, "byoyomi_ms"),
        "fixed_time_ms": pick_int(override_tc, base_tc, "fixed_time_ms"),
        "depth_limit": pick_int(override_tc, base_tc, "depth_limit"),
        "node_limit": pick_int(override_tc, base_tc, "node_limit"),
        "expiry_margin_ms": expiry_margin_ms,
        "allow_timeout": pick_bool(override_tc, base_tc, "allow_timeout", default=False),
    }

    max_wait = pick_int(override_tc, base_tc, "max_wait_ms")
    if max_wait is not None:
        limits_kwargs["max_wait_ms"] = max_wait

    limits = TimeControlLimits(**limits_kwargs)

    inc = int(limits.increment_ms or 0)
    byo = int(limits.byoyomi_ms or 0)
    main = limits.time_ms
    max_inc_byo = 99_900
    max_main = 6_099_900
    if inc > 0 and inc > max_inc_byo:
        raise ValueError(f"increment_ms must be <= 99.9s (99900 ms), got {inc} ms")
    if byo > 0 and byo > max_inc_byo:
        raise ValueError(f"byoyomi_ms must be <= 99.9s (99900 ms), got {byo} ms")
    if main is not None and int(main) > max_main:
        raise ValueError(f"time_ms must be <= 99m99.9s (6099900 ms), got {main} ms")
    return limits


def time_control_limits_to_dict(limits: TimeControlLimits | None) -> dict[str, int | bool | None]:
    """Convert ``TimeControlLimits`` to a JSON-serialisable dictionary."""
    if limits is None:
        raise ValueError("time control limits must be provided")
    return {
        "time_ms": limits.time_ms,
        "increment_ms": limits.increment_ms,
        "byoyomi_ms": limits.byoyomi_ms,
        "fixed_time_ms": limits.fixed_time_ms,
        "depth_limit": limits.depth_limit,
        "node_limit": limits.node_limit,
        "expiry_margin_ms": getattr(limits, "expiry_margin_ms", None),
        "allow_timeout": getattr(limits, "allow_timeout", False),
        "max_wait_ms": getattr(limits, "max_wait_ms", 600_000),
    }


def compute_time_control_from_rules(rules: Any, engines: list[Any]) -> tuple[TimeControlLimits | None, bool]:
    """Compute global ``TimeControlLimits`` and enable flag from rules/engines."""
    tc_limits = None
    enable_time_control = False
    tc = getattr(rules, "time_control", None)
    has_override = any(getattr(engine, "time_control", None) is not None for engine in engines)
    if tc is not None:
        tc_limits = TimeControlLimits(
            time_ms=getattr(tc, "time_ms", None),
            increment_ms=getattr(tc, "increment_ms", None),
            byoyomi_ms=getattr(tc, "byoyomi_ms", None),
            fixed_time_ms=getattr(tc, "fixed_time_ms", None),
            expiry_margin_ms=getattr(tc, "expiry_margin_ms", 500),
            allow_timeout=getattr(tc, "allow_timeout", False),
            depth_limit=getattr(tc, "depth_limit", None),
            node_limit=getattr(tc, "node_limit", None),
        )
        enable_time_control = True
    elif has_override:
        enable_time_control = True
    return tc_limits, enable_time_control


def compute_max_ply_extra_options(rules: Any) -> dict[str, Any] | None:
    """Build extra engine options to keep engine max-move limits in sync."""
    adj_settings = getattr(rules, "adjudication", None)
    if adj_settings is None:
        return None
    if not getattr(adj_settings, "enable_max_plies", False):
        return None
    if not getattr(adj_settings, "sync_max_plies_with_engine", True):
        return None

    moves_value = int(adj_settings.max_plies)
    names_cfg = getattr(adj_settings, "engine_max_ply_option_names", "auto")
    option_names: list[str] = []
    if isinstance(names_cfg, str):
        if names_cfg.lower() == "auto":
            option_names = ["MaxMovesToDraw"]
        else:
            option_names = [part.strip() for part in names_cfg.replace("|", ",").split(",") if part.strip()]
    else:
        for entry in names_cfg:
            if isinstance(entry, str):
                option_names.extend(part.strip() for part in entry.replace("|", ",").split(",") if part.strip())
            else:
                option_names.append(str(entry))

    seen: set[str] = set()
    deduped: list[str] = []
    for name in option_names:
        if name and name not in seen:
            seen.add(name)
            deduped.append(name)
    if not deduped:
        return None
    key = "|".join(deduped)
    return {key: moves_value}


def create_game_runner_from_rules(
    rules: Any,
    engines: list[Any],
    progress_queue: asyncio.Queue[tuple[int, int, str | None]] | None,
) -> GameRunner:
    """Create a ``GameRunner`` configured from tournament/SPSA rules."""
    tc_limits, _ = compute_time_control_from_rules(rules, engines)

    adjudication_cfg: AdjudicationConfig | None = None
    adj_settings = getattr(rules, "adjudication", None)
    if adj_settings is not None:
        enable_resign = bool(getattr(adj_settings, "enable_resign", False))
        enable_max_plies = bool(getattr(adj_settings, "enable_max_plies", False))
        max_plies_value = getattr(adj_settings, "max_plies", None)
        if enable_resign or enable_max_plies:
            adjudication_cfg = AdjudicationConfig(
                resign_enabled=enable_resign,
                resign_score_cp=int(getattr(adj_settings, "resign_score_cp", 800)),
                resign_move_count=int(getattr(adj_settings, "resign_move_count", 8)),
                resign_two_sided=bool(getattr(adj_settings, "resign_two_sided", True)),
                max_plies_enabled=enable_max_plies,
                max_plies=int(max_plies_value) if enable_max_plies and max_plies_value is not None else 0,
            )

    return GameRunner(
        progress_queue=progress_queue,
        time_control_limits=tc_limits,
        adjudication_config=adjudication_cfg,
        repetition_occurrences_to_draw=int(getattr(rules, "repetition_occurrences_to_draw", 2)),
    )


def build_engine_config_map(engines: list[Any]) -> dict[str, Any]:
    """Build a name-to-engine-spec map from a list of engine specs."""
    return {engine.name: engine for engine in engines}


def detect_git_remote_and_ref() -> tuple[str, str]:
    """Return the repository remote URL and current HEAD commit."""
    try:
        remote_url = subprocess.check_output(["git", "config", "--get", "remote.origin.url"], text=True).strip()
        head_ref = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Failed to read git remote information") from exc
    if not remote_url or not head_ref:
        raise RuntimeError("Git remote URL or HEAD ref is empty")
    return remote_url, head_ref


def create_progress_queue() -> asyncio.Queue[tuple[int, int, str | None]]:
    """Factory for the progress queue shared by orchestrators."""
    return asyncio.Queue()


def max_plies_from_rules(rules: Any) -> int:
    """Extract max plies from adjudication settings when enabled."""
    adj_settings = getattr(rules, "adjudication", None)
    if adj_settings is None or not getattr(adj_settings, "enable_max_plies", False):
        return 0
    value = getattr(adj_settings, "max_plies", None)
    return int(value) if value is not None else 0


def remote_project_root(remote_instance: Any) -> str:
    """Return the remote project root for an SSH instance (defaults to $HOME)."""
    instance_config = getattr(remote_instance, "config", None)
    candidate = getattr(instance_config, "project_root", None) if instance_config is not None else None
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return "$HOME/ShogiArena-remote"


def determine_event_move_count(event: dict[str, Any], fallback_move_count: int) -> int:
    """Determine the move count used for dashboard progress routing."""
    event_type = event.get("type")
    if event_type == "move_progress":
        try:
            return int(event.get("ply", 0) or 0)
        except (TypeError, ValueError):
            return fallback_move_count
    return fallback_move_count


def enqueue_progress_event(
    progress_queue: asyncio.Queue[tuple[int, int, str | None]] | None,
    game_id: str,
    event: dict[str, Any],
    *,
    fallback_move_count: int = 0,
    allow_default_str: bool = False,
) -> None:
    """Serialize a remote event and enqueue it for dashboard progress."""
    if progress_queue is None:
        return
    move_count = determine_event_move_count(event, fallback_move_count)
    dumps_kwargs: dict[str, Any] = {"ensure_ascii": False}
    if allow_default_str:
        dumps_kwargs["default"] = str
    payload = json.dumps(event, **dumps_kwargs)
    progress_queue.put_nowait((numeric_game_id(game_id), move_count, payload))


def make_initial_snapshot(
    progress: dict[str, Any],
    generation: int | None = None,
    name_default: str = "Unknown",
) -> dict[str, Any]:
    """Create a minimal worker snapshot structure from a progress payload."""
    initial_sfen = progress.get("initial_sfen")
    if not isinstance(initial_sfen, str) or not initial_sfen.strip():
        raise ValueError("progress payload missing required initial_sfen")
    snapshot: dict[str, Any] = {
        "game_id": progress.get("game_id", ""),
        "initial_sfen": initial_sfen,
        "black_name": progress.get("black_name", name_default),
        "white_name": progress.get("white_name", name_default),
        "moves": [],
        "ki2_moves": [],
        "eval_black": [],
        "eval_white": [],
        "nodes_values": [],
        "depth_values": [],
        "seldepth_values": [],
        "move_times_ms": [],
        "wall_times_ms": [],
        "latency_deltas_ms": [],
        "latency_alerts": [],
        "currentPly": 0,
        "sfen": initial_sfen,
    }
    if generation is not None:
        snapshot["_generation"] = generation
    return snapshot


def apply_move_progress(snapshot: dict[str, Any], progress: dict[str, Any]) -> None:
    """Apply a move_progress update to a dashboard snapshot."""
    # IMPORTANT:
    # The dashboard snapshot represents the game *from `initial_sfen`*.
    # Many producers report `ply` as an absolute ply counter from the original startpos,
    # but the dashboard's `moves[]` is a contiguous list from `initial_sfen`.
    # Therefore `currentPly` must be derived from `len(moves)` (relative), not from `progress["ply"]`.
    if progress.get("move"):
        moves = list(snapshot.get("moves") or [])
        moves.append(progress["move"])
        snapshot["moves"] = moves
    if progress.get("ki2_move"):
        ki2_moves = list(snapshot.get("ki2_moves") or [])
        ki2_moves.append(progress["ki2_move"])
        snapshot["ki2_moves"] = ki2_moves

    eval_cp = progress.get("eval_cp")
    if eval_cp is not None:
        # Use relative ply (from initial_sfen) for parity calculations.
        moves_played = len(list(snapshot.get("moves") or []))
        eb = list(snapshot.get("eval_black") or [])
        ew = list(snapshot.get("eval_white") or [])
        sfen0 = str(snapshot.get("initial_sfen") or "startpos").strip()
        parts0 = sfen0.split(" ")
        start_is_black = len(parts0) < 2 or parts0[1] == "b"

        if moves_played == 0:
            mover_is_black = start_is_black
        else:
            mover_is_black = (moves_played % 2 == 1) if start_is_black else (moves_played % 2 == 0)

        if mover_is_black:
            eb.append(int(eval_cp))
            ew.append(-int(eval_cp))
        else:
            eb.append(-int(eval_cp))
            ew.append(int(eval_cp))
        snapshot["eval_black"] = eb
        snapshot["eval_white"] = ew

    if progress.get("nodes") is not None:
        nodes_values = list(snapshot.get("nodes_values") or [])
        nodes_values.append(progress.get("nodes"))
        snapshot["nodes_values"] = nodes_values
    if progress.get("depth") is not None:
        depth_values = list(snapshot.get("depth_values") or [])
        depth_values.append(progress.get("depth"))
        snapshot["depth_values"] = depth_values
    if progress.get("seldepth") is not None:
        seldepth_values = list(snapshot.get("seldepth_values") or [])
        seldepth_values.append(progress.get("seldepth"))
        snapshot["seldepth_values"] = seldepth_values
    if progress.get("time_ms") is not None:
        move_times = list(snapshot.get("move_times_ms") or [])
        move_times.append(progress.get("time_ms"))
        snapshot["move_times_ms"] = move_times
    if "wall_time_ms" in progress:
        wall_times = list(snapshot.get("wall_times_ms") or [])
        wall_times.append(progress.get("wall_time_ms"))
        snapshot["wall_times_ms"] = wall_times
    if "latency_ms" in progress:
        latency_values = list(snapshot.get("latency_deltas_ms") or [])
        latency_values.append(progress.get("latency_ms"))
        snapshot["latency_deltas_ms"] = latency_values
    if "latency_alert" in progress:
        alerts = list(snapshot.get("latency_alerts") or [])
        alerts.append(bool(progress.get("latency_alert")))
        snapshot["latency_alerts"] = alerts

    if progress.get("result_code") is not None:
        snapshot["result_code"] = progress.get("result_code")

    snapshot["currentPly"] = len(list(snapshot.get("moves") or []))
    if progress.get("sfen"):
        snapshot["sfen"] = progress.get("sfen")


def build_move_diff_payload(
    progress: dict[str, Any], snapshot: dict[str, Any], fallback_game_id: str | int
) -> dict[str, Any]:
    """Build a diff payload for move_progress events."""
    initial_sfen = snapshot.get("initial_sfen")
    if not isinstance(initial_sfen, str) or not initial_sfen.strip():
        raise ValueError("worker snapshot missing required initial_sfen")
    return {
        "game_id": progress.get("game_id", str(fallback_game_id)),
        "initial_sfen": initial_sfen,
        "black_name": snapshot.get("black_name", "Unknown"),
        "white_name": snapshot.get("white_name", "Unknown"),
        # Use the snapshot's relative ply counter (from initial_sfen), not the producer's absolute ply.
        "currentPly": snapshot.get("currentPly", 0),
        "move": progress.get("move"),
        "ki2_move": progress.get("ki2_move"),
        "eval": progress.get("eval_cp"),
        "sfen": progress.get("sfen"),
        "depth": progress.get("depth"),
        "seldepth": progress.get("seldepth"),
        "nodes": progress.get("nodes"),
        "time_ms": progress.get("time_ms"),
        "wall_time_ms": progress.get("wall_time_ms"),
        "latency_ms": progress.get("latency_ms"),
        "latency_alert": progress.get("latency_alert"),
        "result_code": progress.get("result_code"),
    }


def build_clock_start_diff_payload(progress: dict[str, Any], game_id_fallback: str | int) -> dict[str, Any]:
    """Build a diff payload for clock_start events."""
    return {
        "type": "clock_start",
        "game_id": progress.get("game_id", str(game_id_fallback)),
        "active": progress.get("active"),
        "black_remain_ms": progress.get("black_remain_ms"),
        "white_remain_ms": progress.get("white_remain_ms"),
        "started_at_ms": progress.get("started_at_ms"),
        "initial_sfen": progress.get("initial_sfen"),
        "black_name": progress.get("black_name"),
        "white_name": progress.get("white_name"),
        "time_control_black": progress.get("time_control_black"),
        "time_control_white": progress.get("time_control_white"),
        "byoyomi_ms_black": progress.get("byoyomi_ms_black"),
        "byoyomi_ms_white": progress.get("byoyomi_ms_white"),
        "increment_ms_black": progress.get("increment_ms_black"),
        "increment_ms_white": progress.get("increment_ms_white"),
    }


def build_clock_increment_diff_payload(progress: dict[str, Any], game_id_fallback: str | int) -> dict[str, Any]:
    """Build a diff payload for clock_increment events."""
    return {
        "type": "clock_increment",
        "game_id": progress.get("game_id", str(game_id_fallback)),
        "side": progress.get("side"),
        "applied_increment_ms": progress.get("applied_increment_ms"),
        "pre_black_remain_ms": progress.get("pre_black_remain_ms"),
        "pre_white_remain_ms": progress.get("pre_white_remain_ms"),
        "black_remain_ms": progress.get("black_remain_ms"),
        "white_remain_ms": progress.get("white_remain_ms"),
        "occurred_at_ms": progress.get("occurred_at_ms"),
    }


def build_remote_game_info(
    *,
    start_sfen: str,
    moves: Sequence[str],
    result_code: int | None,
    game_id: str,
    black_name: str,
    white_name: str,
    black_limits: TimeControlLimits,
    white_limits: TimeControlLimits,
    move_times: Sequence[int | None] | None = None,
    wall_times: Sequence[int | None] | None = None,
    latency_deltas: Sequence[int | None] | None = None,
    nodes: Sequence[int | None] | None = None,
    depth: Sequence[int | None] | None = None,
    seldepth: Sequence[int | None] | None = None,
    evals: Sequence[int | None] | None = None,
) -> GameInfo:
    """Construct ``GameInfo`` from remote move_progress streams."""

    raw_moves = list(moves)
    moves: list[int] = []
    board = cshogi.Board()
    normalized_sfen = normalize_sfen(start_sfen)
    if normalized_sfen != "startpos":
        board.set_sfen(normalized_sfen)
    for move in raw_moves:
        mv = int(move) if isinstance(move, int) else board.move_from_usi(str(move))
        if mv == 0 or not board.is_legal(mv):
            raise RuntimeError(f"Illegal move in final payload: {move}")
        moves.append(mv)
        board.push(mv)
    moves.append(MOVE_END)

    if result_code is None:
        raise RuntimeError("Missing result_code in remote move_progress events")
    game_result = GameResult(int(result_code))

    num_plies = len(moves) - 1

    def _pad(seq: Sequence[int | None] | None) -> list[int | None] | None:
        if seq is None:
            return None
        data = list(seq)
        while len(data) < num_plies:
            data.append(None)
        return data[:num_plies]

    eval_source: Sequence[int | None] | None
    eval_source = evals

    move_times_core = _pad(move_times)
    wall_times_core = _pad(wall_times)
    latency_core = _pad(latency_deltas)
    nodes_core = _pad(nodes)
    depth_core = _pad(depth)
    seldepth_core = _pad(seldepth)
    eval_core = _pad(eval_source)

    return GameInfo(
        game_name=game_id,
        game_type="arena",
        black_player_name=black_name,
        white_player_name=white_name,
        start_date=datetime.now(),
        end_date=datetime.now(),
        game_result=game_result,
        init_position_sfen=(normalized_sfen if normalized_sfen != "startpos" else cshogi.STARTING_SFEN),
        moves=moves,
        eval_values=(eval_core + [None]) if eval_core is not None else None,
        move_times_ms=(move_times_core + [None]) if move_times_core is not None else None,
        wall_times_ms=(wall_times_core + [None]) if wall_times_core is not None else None,
        latency_deltas_ms=(latency_core + [None]) if latency_core is not None else None,
        nodes_values=(nodes_core + [None]) if nodes_core is not None else None,
        depth_values=(depth_core + [None]) if depth_core is not None else None,
        seldepth_values=(seldepth_core + [None]) if seldepth_core is not None else None,
        black_time_control=black_limits.to_spec_str(),
        white_time_control=white_limits.to_spec_str(),
    )


def compute_pool_capacity(num_workers: int, same_name: bool) -> int:
    """Compute engine pool capacity based on worker count and engine symmetry."""
    cap = num_workers * (2 if same_name else 1)
    return max(2, cap)


def make_role_pool_key(name: str, role: str) -> str:
    """Return the pool key used to differentiate role-assigned engines."""
    return f"{name}#{role}"


def write_engine_yaml(
    out_dir: Path,
    filename: str,
    *,
    name: str,
    engine_binary_path: str,
    options: dict[str, Any] | None,
) -> Path:
    """Write a minimal engine YAML for EngineFactory consumption."""
    payload: dict[str, Any] = {"name": name, "engine_path": engine_binary_path}
    if options:
        payload["options"] = options
    path = out_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


__all__ = [
    "numeric_game_id",
    "build_usi_options",
    "build_time_control_limits",
    "time_control_limits_to_dict",
    "compute_time_control_from_rules",
    "compute_max_ply_extra_options",
    "create_game_runner_from_rules",
    "build_engine_config_map",
    "detect_git_remote_and_ref",
    "create_progress_queue",
    "max_plies_from_rules",
    "remote_project_root",
    "determine_event_move_count",
    "enqueue_progress_event",
    "make_initial_snapshot",
    "apply_move_progress",
    "build_move_diff_payload",
    "build_clock_start_diff_payload",
    "build_clock_increment_diff_payload",
    "build_remote_game_info",
    "compute_pool_capacity",
    "make_role_pool_key",
    "write_engine_yaml",
]
