"""Utility helpers shared by arena orchestrators.

This module hosts stateless helpers that were previously exposed as
``BaseOrchestrator`` staticmethods. Splitting them out keeps the base class
focused on coordinating shared state while still providing a single import
point for tournament/SPSA orchestrators.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import subprocess
import time
import zlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import TypedDict

import rshogi
import yaml
from rshogi.core import Board, Move, normalize_usi_position

from shogiarena.arena.configs.base import RulesConfig
from shogiarena.arena.configs.tournament import EngineConfig
from shogiarena.arena.engines.time_control import TimeControlLimits, limits_to_record_time_spec
from shogiarena.arena.execution.game_runner import GameRunner
from shogiarena.arena.instances.models import Instance
from shogiarena.arena.services.game_control.adjudication import AdjudicationConfig
from shogiarena.utils.common import project_dirs
from shogiarena.utils.common.paths import resolve_path_like
from shogiarena.utils.types.coerce import coerce_int as _coerce_int
from shogiarena.utils.types.coerce import coerce_str
from shogiarena.utils.types.snapshots import (
    EngineStatusEntry,
    EngineStatusMap,
    MoveProgressEvent,
    WorkerSnapshot,
)
from shogiarena.utils.types.types import STARTING_SFEN, GameResult, game_result_terminal_kind

logger = logging.getLogger(__name__)

_OVERLAY_CACHE: dict[Path, dict[str, object]] = {}
_OVERLAY_PAYLOAD_CACHE: dict[Path, dict[str, object]] = {}

_ENGINE_STATUS_ROLES = ("black", "white")
_ENGINE_IO_TAIL_LIMIT = 200


class HandshakeDiffPayload(TypedDict):
    """ハンドシェイクイベントの diff ペイロード。"""

    engine_status: EngineStatusMap


class MoveDiffPayload(TypedDict, total=False):
    """手の進行イベントの diff ペイロード。"""

    game_id: str
    initial_sfen: str
    black_name: str
    white_name: str
    currentPly: int
    move: str | None
    ki2_move: str | None
    eval: int | None
    sfen: str | None
    depth: int | None
    seldepth: int | None
    nodes: int | None
    time_ms: int | None
    wall_time_ms: int | None
    latency_ms: int | None
    latency_alert: bool | None
    result_code: int | None
    engine_status: EngineStatusMap | None


class ClockStartDiffPayload(TypedDict, total=False):
    """時計開始イベントの diff ペイロード。"""

    type: str
    game_id: str
    active: str | None
    black_remain_ms: int | None
    white_remain_ms: int | None
    started_at_ms: int | None
    initial_sfen: str | None
    black_name: str | None
    white_name: str | None
    time_control_black: str | None
    time_control_white: str | None
    byoyomi_ms_black: int | None
    byoyomi_ms_white: int | None
    increment_ms_black: int | None
    increment_ms_white: int | None


class ClockIncrementDiffPayload(TypedDict, total=False):
    """時計加算イベントの diff ペイロード。"""

    type: str
    game_id: str
    side: str | None
    applied_increment_ms: int | None
    pre_black_remain_ms: int | None
    pre_white_remain_ms: int | None
    black_remain_ms: int | None
    white_remain_ms: int | None
    occurred_at_ms: int | None


class GameAssignedDiffPayload(TypedDict, total=False):
    """対局割当イベントの diff ペイロード。"""

    type: str
    game_id: str
    initial_sfen: str
    black_name: str | None
    white_name: str | None
    engine_status: EngineStatusMap | None
    time_control_black: str | None
    time_control_white: str | None


# EngineIoTailEntry, EngineStatusEntry, EngineStatusMap,
# _WorkerSnapshotRequired, WorkerSnapshot, MoveProgressEvent
# → shogiarena.utils.types.snapshots から import 済み


# --- Identifier helpers ---------------------------------------------------


def numeric_game_id(game_id: str | int) -> int:
    """Convert a game identifier to a stable non-negative integer."""
    if isinstance(game_id, int):
        return game_id
    if isinstance(game_id, str) and game_id.startswith("game_"):
        return int(game_id.split("_", 1)[1])
    return zlib.crc32(str(game_id).encode("utf-8")) & 0x7FFFFFFF


# --- Engine option helpers -------------------------------------------------


def _load_overlay_payload(path: Path) -> dict[str, object]:
    cached = _OVERLAY_PAYLOAD_CACHE.get(path)
    if cached is not None:
        return dict(cached)
    if not path.exists():
        raise FileNotFoundError(f"overlay config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TypeError(f"overlay YAML must be a mapping: {path}")
    _OVERLAY_PAYLOAD_CACHE[path] = dict(raw)
    return dict(raw)


def _extract_overlay_options(payload: Mapping[str, object], *, path: Path) -> dict[str, object]:
    overlay_opts = payload.get("options") if "options" in payload else payload
    if not isinstance(overlay_opts, Mapping):
        raise TypeError(f"overlay options must be a mapping: {path}")
    return {str(key): value for key, value in overlay_opts.items()}


def _load_overlay_options(path: Path) -> dict[str, object]:
    cached = _OVERLAY_CACHE.get(path)
    if cached is not None:
        return dict(cached)
    payload = _load_overlay_payload(path)
    overlay_opts = _extract_overlay_options(payload, path=path)
    _OVERLAY_CACHE[path] = dict(overlay_opts)
    return dict(overlay_opts)


def _apply_engine_overlay(engine_spec: EngineConfig, payload: Mapping[str, object]) -> None:
    engine_payload = payload.get("engine")
    if not isinstance(engine_payload, Mapping):
        return
    engine_values = {str(key): value for key, value in engine_payload.items()}
    for key in (
        "mate_default_ply_limit",
        "mate_default_node_limit",
        "mate_default_infinite",
        "mate_wait_for_bestmove",
        "isready_sync_strategy",
        "isready_lock_key",
        "isready_lock_template",
        "isready_lock_check_key",
        "isready_lock_check_template",
        "isready_lock_check_templates",
        "isready_lock_skip_if_exists",
    ):
        current = getattr(engine_spec, key, None)
        if isinstance(current, str) and current.strip():
            continue
        if (
            key in {"mate_default_infinite", "mate_wait_for_bestmove", "isready_lock_skip_if_exists"}
            and current is not None
        ):
            continue
        if key in {"mate_default_ply_limit", "mate_default_node_limit"} and current is not None and current > 0:
            continue
        value = engine_values.get(key)
        if isinstance(value, str) and value.strip():
            setattr(engine_spec, key, value.strip())
        elif key in {"mate_default_infinite", "mate_wait_for_bestmove", "isready_lock_skip_if_exists"} and isinstance(
            value, bool
        ):
            setattr(engine_spec, key, value)
        elif key in {"mate_default_ply_limit", "mate_default_node_limit"} and isinstance(value, int) and value > 0:
            setattr(engine_spec, key, value)
        elif key == "isready_lock_check_templates" and isinstance(value, list):
            cleaned = [item for item in value if isinstance(item, str) and item.strip()]
            if cleaned:
                setattr(engine_spec, key, tuple(cleaned))


def build_usi_options(base_extra: dict[str, object] | None, engine_spec: EngineConfig) -> dict[str, object] | None:
    """Merge arena-level extra options with engine-specific overlays/options."""
    overlay: dict[str, object] = {}
    artifact = engine_spec.artifact
    if artifact and artifact.strip():
        repo_name = artifact.split("/", 1)[0]
        overlay_path = project_dirs.overlays.get(repo_name)
        if overlay_path is not None:
            payload = _load_overlay_payload(overlay_path)
            _apply_engine_overlay(engine_spec, payload)
            overlay.update(_extract_overlay_options(payload, path=overlay_path))

    base = base_extra or {}
    for overlay_path in engine_spec.options_overlays or []:
        payload = _load_overlay_payload(overlay_path)
        _apply_engine_overlay(engine_spec, payload)
    overlay_opts = engine_spec.load_overlay_options() or {}
    inline_opts = {str(k): v for k, v in engine_spec.options.items()} if engine_spec.options else {}

    if not overlay and not base and not overlay_opts and not inline_opts:
        return None
    merged: dict[str, object] = dict(overlay)
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


def build_time_control_limits(
    base_tc: TimeControlLimits | None, override_tc: TimeControlLimits | None
) -> TimeControlLimits | None:
    """Combine base and per-engine time control into final limits with validation."""
    if base_tc is None and override_tc is None:
        return None

    def pick_int(over: TimeControlLimits | None, base: TimeControlLimits | None, name: str) -> int | None:
        if over is not None:
            value = getattr(over, name, None)
            if value is not None:
                return int(value)
        if base is not None:
            value = getattr(base, name, None)
            if value is not None:
                return int(value)
        return None

    expiry_margin_ms = (
        override_tc.expiry_margin_ms
        if override_tc is not None
        else base_tc.expiry_margin_ms
        if base_tc is not None
        else 500
    )

    time_ms = pick_int(override_tc, base_tc, "time_ms")
    increment_ms = pick_int(override_tc, base_tc, "increment_ms")
    byoyomi_ms = pick_int(override_tc, base_tc, "byoyomi_ms")
    fixed_time_ms = pick_int(override_tc, base_tc, "fixed_time_ms")
    depth_limit = pick_int(override_tc, base_tc, "depth_limit")
    node_limit = pick_int(override_tc, base_tc, "node_limit")
    allow_timeout = (
        override_tc.allow_timeout
        if override_tc is not None
        else base_tc.allow_timeout
        if base_tc is not None
        else False
    )
    max_wait = pick_int(override_tc, base_tc, "max_wait_ms")
    limits = TimeControlLimits(
        time_ms=time_ms,
        increment_ms=increment_ms,
        byoyomi_ms=byoyomi_ms,
        fixed_time_ms=fixed_time_ms,
        depth_limit=depth_limit,
        node_limit=node_limit,
        expiry_margin_ms=expiry_margin_ms,
        allow_timeout=allow_timeout,
        max_wait_ms=max_wait if max_wait is not None else TimeControlLimits.max_wait_ms,
    )

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
        "expiry_margin_ms": limits.expiry_margin_ms,
        "allow_timeout": limits.allow_timeout,
        "max_wait_ms": limits.max_wait_ms,
    }


def compute_time_control_from_rules(
    rules: RulesConfig, engines: list[EngineConfig]
) -> tuple[TimeControlLimits | None, bool]:
    """Compute global ``TimeControlLimits`` and enable flag from rules/engines."""
    tc_limits = None
    enable_time_control = False
    tc = rules.time_control
    has_override = any(engine.time_control is not None for engine in engines)
    if tc is not None:
        tc_limits = TimeControlLimits(
            time_ms=tc.time_ms,
            increment_ms=tc.increment_ms,
            byoyomi_ms=tc.byoyomi_ms,
            fixed_time_ms=tc.fixed_time_ms,
            expiry_margin_ms=tc.expiry_margin_ms,
            allow_timeout=tc.allow_timeout,
            depth_limit=tc.depth_limit,
            node_limit=tc.node_limit,
        )
        enable_time_control = True
    elif has_override:
        enable_time_control = True
    return tc_limits, enable_time_control


def compute_max_ply_extra_options(rules: RulesConfig) -> dict[str, object] | None:
    """Build extra engine options to keep engine max-move limits in sync."""
    adj_settings = rules.adjudication
    if not adj_settings.enable_max_plies:
        return None
    if not adj_settings.sync_max_plies_with_engine:
        return None

    moves_value = int(adj_settings.max_plies) if adj_settings.max_plies is not None else 0
    names_cfg = adj_settings.engine_max_ply_option_names
    option_names: list[str] = []
    if isinstance(names_cfg, str):
        if names_cfg.lower() == "auto":
            option_names = ["MaxMovesToDraw", "Draw_Ply"]
        else:
            option_names = [part.strip() for part in names_cfg.replace("|", ",").split(",") if part.strip()]
    else:
        for entry in names_cfg:
            option_names.extend(part.strip() for part in entry.replace("|", ",").split(",") if part.strip())

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
    rules: RulesConfig,
    engines: list[EngineConfig],
    progress_queue: asyncio.Queue[tuple[int, int, str | None]] | None,
) -> GameRunner:
    """Create a ``GameRunner`` configured from tournament/SPSA rules."""
    tc_limits, _ = compute_time_control_from_rules(rules, engines)

    adjudication_cfg: AdjudicationConfig | None = None
    adj_settings = rules.adjudication
    resign_threshold = adj_settings.resign_threshold_cp
    enable_resign = resign_threshold is not None
    enable_max_plies = adj_settings.enable_max_plies
    max_plies_value = adj_settings.max_plies
    if enable_resign or enable_max_plies:
        adjudication_cfg = AdjudicationConfig(
            resign_enabled=enable_resign,
            resign_score_cp=int(resign_threshold) if enable_resign and resign_threshold is not None else 0,
            resign_move_count=adj_settings.resign_move_count,
            resign_two_sided=adj_settings.resign_two_sided,
            max_plies_enabled=enable_max_plies,
            max_plies=int(max_plies_value) if enable_max_plies and max_plies_value is not None else 0,
        )

    return GameRunner(
        progress_queue=progress_queue,
        time_control_limits=tc_limits,
        adjudication_config=adjudication_cfg,
        repetition_occurrences_to_draw=int(getattr(rules, "repetition_occurrences_to_draw", 2)),
    )


def build_engine_config_map(engines: list[EngineConfig]) -> dict[str, EngineConfig]:
    """Build a name-to-engine-spec map from a list of engine specs."""
    mapped: dict[str, EngineConfig] = {}
    for engine in engines:
        name = engine.name
        if not name:
            raise ValueError("Engine config must have a non-empty name")
        mapped[name] = engine
    return mapped


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


def max_plies_from_rules(rules: object) -> int:
    """Extract max plies from adjudication settings when enabled."""
    adj_settings = getattr(rules, "adjudication", None)
    if adj_settings is None or not getattr(adj_settings, "enable_max_plies", False):
        return 0
    value = getattr(adj_settings, "max_plies", None)
    return int(value) if value is not None else 0


def remote_project_root(remote_instance: Instance) -> str:
    """Return the remote project root for an SSH instance (defaults to $HOME)."""
    instance_config = getattr(remote_instance, "config", None)
    candidate = getattr(instance_config, "project_root", None) if instance_config is not None else None
    if s := coerce_str(candidate):
        return s
    return "$HOME/ShogiArena-remote"


def determine_event_move_count(event: dict[str, object], fallback_move_count: int) -> int:
    """Determine the move count used for dashboard progress routing."""
    event_type = event.get("type")
    if event_type == "move_progress":
        try:
            return _coerce_int(event.get("ply")) or 0
        except (TypeError, ValueError):
            return fallback_move_count
    return fallback_move_count


def enqueue_progress_event(
    progress_queue: asyncio.Queue[tuple[int, int, str | None]] | None,
    game_id: str,
    event: dict[str, object],
    *,
    fallback_move_count: int = 0,
    allow_default_str: bool = False,
) -> None:
    """Serialize a remote event and enqueue it for dashboard progress."""
    if progress_queue is None:
        return
    move_count = determine_event_move_count(event, fallback_move_count)
    if allow_default_str:
        payload = json.dumps(event, ensure_ascii=False, default=str)
    else:
        payload = json.dumps(event, ensure_ascii=False)
    progress_queue.put_nowait((numeric_game_id(game_id), move_count, payload))


def make_initial_snapshot(
    progress: Mapping[str, object],
    generation: int | None = None,
    name_default: str = "Unknown",
) -> WorkerSnapshot:
    """Create a minimal worker snapshot structure from a progress payload."""
    initial_sfen = coerce_str(progress.get("initial_sfen"))
    if not initial_sfen:
        raise ValueError("progress payload missing required initial_sfen")
    game_id_raw = progress.get("game_id", "")
    black_name_raw = progress.get("black_name", name_default)
    white_name_raw = progress.get("white_name", name_default)
    snapshot: WorkerSnapshot = {
        "game_id": str(game_id_raw) if game_id_raw else "",
        "initial_sfen": initial_sfen,
        "black_name": coerce_str(black_name_raw) or name_default,
        "white_name": coerce_str(white_name_raw) or name_default,
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


def parse_move_progress(raw: Mapping[str, object]) -> MoveProgressEvent:
    """JSON デシリアライズ済みの raw dict から検証済み ``MoveProgressEvent`` を生成する。

    境界での isinstance / ``coerce_int`` チェックをここに集約し、
    ``apply_move_progress`` 等の呼び出し側から型検証を除去する。
    """
    event: MoveProgressEvent = {}
    gid = raw.get("game_id")
    if gid is not None:
        event["game_id"] = str(gid)
    val = raw.get("initial_sfen")
    if isinstance(val, str):
        event["initial_sfen"] = val
    val = raw.get("black_name")
    if isinstance(val, str):
        event["black_name"] = val
    val = raw.get("white_name")
    if isinstance(val, str):
        event["white_name"] = val
    if s := coerce_str(raw.get("move")):
        event["move"] = s
    if s := coerce_str(raw.get("ki2_move")):
        event["ki2_move"] = s
    if s := coerce_str(raw.get("sfen")):
        event["sfen"] = s
    n = _coerce_int(raw.get("eval_cp"))
    if n is not None:
        event["eval_cp"] = n
    n = _coerce_int(raw.get("ply"))
    if n is not None:
        event["ply"] = n
    n = _coerce_int(raw.get("nodes"))
    if n is not None:
        event["nodes"] = n
    n = _coerce_int(raw.get("depth"))
    if n is not None:
        event["depth"] = n
    n = _coerce_int(raw.get("seldepth"))
    if n is not None:
        event["seldepth"] = n
    n = _coerce_int(raw.get("time_ms"))
    if n is not None:
        event["time_ms"] = n
    n = _coerce_int(raw.get("wall_time_ms"))
    if n is not None:
        event["wall_time_ms"] = n
    n = _coerce_int(raw.get("latency_ms"))
    if n is not None:
        event["latency_ms"] = n
    n = _coerce_int(raw.get("result_code"))
    if n is not None:
        event["result_code"] = n
    if "latency_alert" in raw:
        event["latency_alert"] = bool(raw["latency_alert"])
    return event


def _default_engine_status_entry() -> EngineStatusEntry:
    return {
        "state": "waiting_for_usiok",
        "io_tail": [],
        "updated_at_ms": int(time.time() * 1000),
    }


def ensure_engine_status(snapshot: WorkerSnapshot) -> EngineStatusMap:
    existing = snapshot.get("engine_status")
    if existing is not None:
        # Already populated — ensure both roles exist.
        for role in _ENGINE_STATUS_ROLES:
            if role not in existing:
                existing[role] = _default_engine_status_entry()
        return existing
    status: EngineStatusMap = {role: _default_engine_status_entry() for role in _ENGINE_STATUS_ROLES}
    snapshot["engine_status"] = status
    return status


def _append_engine_io_tail(entry: EngineStatusEntry, direction: str, line: str, ts: int) -> None:
    tail = entry["io_tail"]
    tail.append({"dir": direction, "line": line, "ts": ts})
    if len(tail) > _ENGINE_IO_TAIL_LIMIT:
        del tail[0 : len(tail) - _ENGINE_IO_TAIL_LIMIT]


def _sanitize_engine_io_line(line: str) -> str:
    return line.strip()


def sanitize_engine_io_line(line: str) -> str:
    """Sanitize engine I/O line for dashboard logging."""
    return _sanitize_engine_io_line(line)


def update_engine_status_entry(
    snapshot: WorkerSnapshot,
    role: str,
    *,
    direction: str | None = None,
    line: str | None = None,
    state: str | None = None,
    timestamp: int | None = None,
) -> EngineStatusMap:
    if role not in _ENGINE_STATUS_ROLES:
        raise ValueError("Invalid engine role for status update")
    status = ensure_engine_status(snapshot)
    entry = status[role]
    ts = int(timestamp if timestamp is not None else time.time() * 1000)
    entry["updated_at_ms"] = ts
    if s := coerce_str(state):
        entry["state"] = s.lower()
    if direction and line:
        _append_engine_io_tail(entry, direction, _sanitize_engine_io_line(line), ts)
    return status


def clone_engine_status(snapshot: WorkerSnapshot) -> EngineStatusMap:
    status = ensure_engine_status(snapshot)
    return copy.deepcopy(status)


def build_handshake_diff_payload(snapshot: WorkerSnapshot) -> dict[str, object]:
    return {"engine_status": clone_engine_status(snapshot)}


def apply_handshake_log_payload(snapshot: WorkerSnapshot, payload: Mapping[str, object]) -> dict[str, object] | None:
    role = payload.get("role")
    if not isinstance(role, str) or role.lower() not in _ENGINE_STATUS_ROLES:
        return None
    direction_raw = payload.get("direction")
    line_raw = payload.get("line")
    state_raw = payload.get("state")
    timestamp = payload.get("ts")
    entry_ts = _coerce_int(timestamp)
    direction = coerce_str(direction_raw)
    line = coerce_str(line_raw)
    state = coerce_str(state_raw)
    update_engine_status_entry(snapshot, role.lower(), direction=direction, line=line, state=state, timestamp=entry_ts)
    return build_handshake_diff_payload(snapshot)


def apply_move_progress(snapshot: WorkerSnapshot, progress: MoveProgressEvent) -> None:
    """Apply a validated move_progress update to a dashboard snapshot.

    ``progress`` は ``parse_move_progress`` で検証済みであるため、
    個々のフィールドに対する isinstance / coerce_int チェックは不要。
    """
    # IMPORTANT:
    # The dashboard snapshot represents the game *from `initial_sfen`*.
    # Many producers report `ply` as an absolute ply counter from the original startpos,
    # but the dashboard's `moves[]` is a contiguous list from `initial_sfen`.
    # Therefore `currentPly` must be derived from `len(moves)` (relative), not from `progress["ply"]`.
    move = progress.get("move")
    if move:
        snapshot["moves"].append(move)
    ki2_move = progress.get("ki2_move")
    if ki2_move:
        snapshot["ki2_moves"].append(ki2_move)

    eval_cp = progress.get("eval_cp")
    if eval_cp is not None:
        # Use relative ply (from initial_sfen) for parity calculations.
        moves_played = len(snapshot["moves"])
        eb = snapshot["eval_black"]
        ew = snapshot["eval_white"]
        sfen0 = snapshot["initial_sfen"].strip() or "startpos"
        parts0 = sfen0.split(" ")
        start_is_black = len(parts0) < 2 or parts0[1] == "b"

        if moves_played == 0:
            mover_is_black = start_is_black
        else:
            mover_is_black = (moves_played % 2 == 1) if start_is_black else (moves_played % 2 == 0)

        if mover_is_black:
            eb.append(eval_cp)
            ew.append(-eval_cp)
        else:
            eb.append(-eval_cp)
            ew.append(eval_cp)

    nodes = progress.get("nodes")
    if nodes is not None:
        snapshot["nodes_values"].append(nodes)
    depth = progress.get("depth")
    if depth is not None:
        snapshot["depth_values"].append(depth)
    seldepth = progress.get("seldepth")
    if seldepth is not None:
        snapshot["seldepth_values"].append(seldepth)
    time_ms = progress.get("time_ms")
    if time_ms is not None:
        snapshot["move_times_ms"].append(time_ms)
    wall_time_ms = progress.get("wall_time_ms")
    if wall_time_ms is not None:
        snapshot["wall_times_ms"].append(wall_time_ms)
    latency_ms = progress.get("latency_ms")
    if latency_ms is not None:
        snapshot["latency_deltas_ms"].append(latency_ms)
    latency_alert = progress.get("latency_alert")
    if latency_alert is not None:
        snapshot["latency_alerts"].append(latency_alert)

    result_code = progress.get("result_code")
    if result_code is not None:
        snapshot["result_code"] = result_code

    snapshot["currentPly"] = len(snapshot["moves"])
    sfen = progress.get("sfen")
    if sfen:
        snapshot["sfen"] = sfen

    status = ensure_engine_status(snapshot)
    now_ms = int(time.time() * 1000)
    for role in _ENGINE_STATUS_ROLES:
        entry = status[role]
        state = entry["state"]
        if state.lower() not in {"ready", "thinking"}:
            entry["state"] = "ready"
            entry["updated_at_ms"] = now_ms


def build_move_diff_payload(
    progress: MoveProgressEvent, snapshot: WorkerSnapshot, fallback_game_id: str | int
) -> dict[str, object]:
    """Build a diff payload for move_progress events."""
    initial_sfen = snapshot["initial_sfen"]
    if not initial_sfen.strip():
        raise ValueError("worker snapshot missing required initial_sfen")
    engine_status = snapshot.get("engine_status")
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
        "engine_status": engine_status,
    }


def build_clock_start_diff_payload(progress: dict[str, object], game_id_fallback: str | int) -> dict[str, object]:
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
        "black_time_control": progress.get("time_control_black"),
        "white_time_control": progress.get("time_control_white"),
        "byoyomi_ms_black": progress.get("byoyomi_ms_black"),
        "byoyomi_ms_white": progress.get("byoyomi_ms_white"),
        "increment_ms_black": progress.get("increment_ms_black"),
        "increment_ms_white": progress.get("increment_ms_white"),
    }


def build_clock_increment_diff_payload(progress: dict[str, object], game_id_fallback: str | int) -> dict[str, object]:
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


def build_game_assigned_diff_payload(progress: dict[str, object], game_id_fallback: str | int) -> dict[str, object]:
    """Build a diff payload for game_assigned events."""
    initial_sfen = coerce_str(progress.get("initial_sfen"))
    if not initial_sfen:
        raise ValueError("game_assigned payload missing required initial_sfen")
    engine_status = progress.get("engine_status")
    payload: dict[str, object] = {
        "type": "game_assigned",
        "game_id": progress.get("game_id", str(game_id_fallback)),
        "initial_sfen": initial_sfen,
        "black_name": progress.get("black_name"),
        "white_name": progress.get("white_name"),
    }
    if engine_status is not None:
        payload["engine_status"] = engine_status
    tc_black = progress.get("time_control_black")
    tc_white = progress.get("time_control_white")
    if tc_black is not None and tc_white is not None:
        payload["time_control_black"] = tc_black
        payload["time_control_white"] = tc_white
    return payload


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
) -> rshogi.record.GameRecord:
    """Construct ``GameRecord`` from remote move_progress streams."""

    raw_moves = list(moves)
    moves: list[Move] = []
    board = Board()
    normalized_sfen = normalize_usi_position(start_sfen)
    if normalized_sfen != "startpos":
        board.set_sfen(normalized_sfen)
    for move in raw_moves:
        try:
            mv = Move.from_usi(move)
        except ValueError:
            resolved_move = None
        else:
            resolved_move = mv if board.is_legal_move(mv) else None
        if resolved_move is None:
            raise RuntimeError(f"Illegal move in final payload: {move}")
        moves.append(resolved_move)
        board.apply_move(resolved_move)
    if result_code is None:
        raise RuntimeError("Missing result_code in remote move_progress events")
    game_result = GameResult(int(result_code))

    num_plies = len(moves)

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

    now_iso = datetime.now().isoformat()
    tc_black_str = limits_to_record_time_spec(black_limits)
    tc_white_str = limits_to_record_time_spec(white_limits)
    record_metadata = rshogi.record.GameRecordMetadata(
        game_name=game_id,
        game_type="arena",
        black_player=black_name,
        white_player=white_name,
        start_date=now_iso,
        end_date=now_iso,
        updated_date=now_iso,
        black_time_control=rshogi.record.TimeControl.from_spec(tc_black_str),
        white_time_control=rshogi.record.TimeControl.from_spec(tc_white_str),
        attributes={
            "game_name": game_id,
            "game_type": "arena",
            "updated_date": now_iso,
        },
    )
    move_records: list[rshogi.record.MoveRecord] = []
    for idx, move in enumerate(moves):
        wall_time = wall_times_core[idx] if wall_times_core is not None else None
        latency_delta = latency_core[idx] if latency_core is not None else None
        engine_info = rshogi.record.MoveEngineInfo(
            eval=eval_core[idx] if eval_core is not None else None,
            nodes=nodes_core[idx] if nodes_core is not None else None,
            depth=depth_core[idx] if depth_core is not None else None,
            seldepth=seldepth_core[idx] if seldepth_core is not None else None,
            wall_time_ms=int(wall_time) if wall_time is not None else None,
            latency_delta_ms=int(latency_delta) if latency_delta is not None else None,
        )
        move_records.append(
            rshogi.record.MoveRecord(
                move,
                time_ms=move_times_core[idx] if move_times_core is not None else None,
                engine_info=engine_info,
            )
        )
    init_sfen = normalized_sfen if normalized_sfen != "startpos" else STARTING_SFEN
    terminal = rshogi.record.SpecialMoveRecord(game_result_terminal_kind(game_result), game_result)
    return rshogi.record.GameRecord.from_main_line(init_sfen, move_records, terminal, record_metadata)


def compute_pool_capacity(num_workers: int, same_name: bool) -> int:
    """Compute engine pool capacity based on worker count and engine symmetry."""
    cap = num_workers * (2 if same_name else 1)
    return max(2, cap)


def make_role_pool_key(name: str, role: str) -> str:
    """Return the pool key used to differentiate role-assigned engines."""
    return f"{name}#{role}"


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
    "parse_move_progress",
    "apply_move_progress",
    "build_move_diff_payload",
    "build_clock_start_diff_payload",
    "build_clock_increment_diff_payload",
    "build_game_assigned_diff_payload",
    "build_remote_game_info",
    "compute_pool_capacity",
    "make_role_pool_key",
]
