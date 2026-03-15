"""Shared state container for Arena Dashboard.

Provides a centralized container for mutable state shared across API handlers.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field

from shogiarena._core.contexts.dashboard.application.events import GamesSnapshotPayload
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.snapshots import EngineIoTailEntry, EngineOptionsSnapshots, GameSnapshot


@dataclass
class DashboardState:
    """Container for shared mutable state across API handlers.

    This dataclass holds all mutable state that needs to be shared between
    different components of the API server (game cache, broadcast handler,
    snapshot storage, etc.).

    Attributes:
        worker_snapshots: Per-worker snapshot data keyed by worker index.
        worker_assignment: Mapping from worker index to assigned game ID (or None).
        assignment_rev: Monotonically increasing revision for assignment changes.
        game_snapshots: LRU cache of game snapshots keyed by game ID.
        engine_options_snapshot: Runtime USI options per engine name.
        engine_info_snapshot: Engine metadata (id_name, id_author) per engine name.
        summary_snapshots: Per-source summary state (e.g. tournament/spsa/sprt/match).
        games_snapshot: Current games list snapshot.
        engine_io_logs: Per-game engine I/O log buffers (gid -> role -> deque).
    """

    worker_snapshots: dict[int, GameSnapshot] = field(default_factory=dict)
    worker_assignment: dict[int, str | None] = field(default_factory=dict)
    assignment_rev: int = 0
    game_snapshots: OrderedDict[str, GameSnapshot] = field(default_factory=OrderedDict)
    engine_options_snapshot: EngineOptionsSnapshots = field(default_factory=dict)
    engine_info_snapshot: dict[str, dict[str, str]] = field(default_factory=dict)
    # I/O boundary: 各モードのサマリが動的に構築される。型は JsonObject を参照。
    summary_snapshots: dict[str, JsonObject] = field(default_factory=dict)
    games_snapshot: GamesSnapshotPayload | None = None
    engine_io_logs: dict[str, dict[str, deque[EngineIoTailEntry]]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Summary snapshot accessors
    # ------------------------------------------------------------------
    def get_summary_snapshot(self, source: str) -> JsonObject | None:
        """Get summary snapshot by source."""
        return self.summary_snapshots.get(source)

    def set_summary_snapshot(self, source: str, snapshot: JsonObject) -> None:
        """Set summary snapshot by source."""
        self.summary_snapshots[source] = snapshot

    def get_summary_sources(self) -> list[str]:
        """Get all summary source keys."""
        return list(self.summary_snapshots.keys())

    # ------------------------------------------------------------------
    # Assignment state accessors
    # ------------------------------------------------------------------
    def get_assignment_rev(self) -> int:
        """Get current assignment revision."""
        return self.assignment_rev

    def bump_assignment_rev(self) -> int:
        """Increment and return assignment revision."""
        self.assignment_rev += 1
        return self.assignment_rev

    def get_worker_assignment(self, worker_idx: int) -> str | None:
        """Get assignment for one worker."""
        return self.worker_assignment.get(worker_idx)

    def set_worker_assignment(self, worker_idx: int, gid: str | None) -> None:
        """Set assignment for one worker."""
        self.worker_assignment[worker_idx] = gid

    def get_worker_assignments(self) -> dict[int, str | None]:
        """Get worker assignment map."""
        return self.worker_assignment

    # ------------------------------------------------------------------
    # Worker snapshot accessors
    # ------------------------------------------------------------------
    def get_worker_snapshot(self, worker_idx: int) -> GameSnapshot | None:
        """Get worker snapshot."""
        return self.worker_snapshots.get(worker_idx)

    def set_worker_snapshot(self, worker_idx: int, snapshot: GameSnapshot) -> None:
        """Set worker snapshot."""
        self.worker_snapshots[worker_idx] = snapshot

    def clear_worker_snapshots(self) -> None:
        """Clear worker snapshot map."""
        self.worker_snapshots.clear()

    # ------------------------------------------------------------------
    # Game cache accessors
    # ------------------------------------------------------------------
    def get_game_snapshot(self, gid: str) -> GameSnapshot | None:
        """Get game snapshot from cache."""
        return self.game_snapshots.get(gid)

    def set_game_snapshot(self, gid: str, snapshot: GameSnapshot) -> None:
        """Set game snapshot in cache."""
        self.game_snapshots[gid] = snapshot

    def get_game_snapshots(self) -> OrderedDict[str, GameSnapshot]:
        """Get game snapshot cache."""
        return self.game_snapshots

    def get_active_game_ids(self) -> set[str]:
        """Get set of currently assigned game IDs."""
        gids: set[str] = set()
        for value in self.worker_assignment.values():
            if isinstance(value, str) and value:
                gids.add(value)
        return gids

    # ------------------------------------------------------------------
    # Games summary snapshot accessors
    # ------------------------------------------------------------------
    def get_games_snapshot(self) -> GamesSnapshotPayload | None:
        """Get current games snapshot."""
        return self.games_snapshot

    def set_games_snapshot(self, snapshot: GamesSnapshotPayload | None) -> None:
        """Set current games snapshot."""
        self.games_snapshot = snapshot

    # ------------------------------------------------------------------
    # Engine IO log accessors
    # ------------------------------------------------------------------
    def get_engine_io_logs(self, gid: str) -> dict[str, deque[EngineIoTailEntry]]:
        """Get raw engine I/O log map for a game id."""
        logs = self.engine_io_logs.get(gid)
        if not isinstance(logs, dict):
            logs = {}
            self.engine_io_logs[gid] = logs
        return logs

    def ensure_engine_io_buffers(self, gid: str, maxlen: int) -> dict[str, deque[EngineIoTailEntry]]:
        """Ensure both black/white engine I/O deques exist."""
        logs = self.get_engine_io_logs(gid)
        for role in ("black", "white"):
            entry = logs.get(role)
            if not isinstance(entry, deque):
                logs[role] = deque(maxlen=maxlen)
            elif entry.maxlen != maxlen:
                logs[role] = deque(entry, maxlen=maxlen)
        return logs

    def clear_engine_io_logs(self, gid: str) -> None:
        """Clear engine I/O logs for a game id."""
        self.engine_io_logs.pop(gid, None)

    # ------------------------------------------------------------------
    # Engine option accessors
    # ------------------------------------------------------------------
    def get_engine_options_snapshot(self) -> EngineOptionsSnapshots:
        """Get current engine option snapshot map."""
        return self.engine_options_snapshot

    def set_engine_option_snapshot(self, engine_name: str, options: JsonObject) -> None:
        """Set options for one engine."""
        self.engine_options_snapshot[engine_name] = options

    def get_engine_info_snapshot(self) -> dict[str, dict[str, str]]:
        """Get engine metadata snapshot map."""
        return self.engine_info_snapshot

    def set_engine_info_entry(self, engine_name: str, info: dict[str, str]) -> None:
        """Set metadata for one engine."""
        self.engine_info_snapshot[engine_name] = info
