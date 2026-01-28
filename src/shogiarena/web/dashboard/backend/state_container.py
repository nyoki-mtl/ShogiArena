"""Shared state container for Arena Dashboard.

Provides a centralized container for mutable state shared across API handlers.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any


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
    """

    worker_snapshots: dict[int, dict[str, Any]] = field(default_factory=dict)
    worker_assignment: dict[int, str | None] = field(default_factory=dict)
    assignment_rev: int = 0
    game_snapshots: OrderedDict[str, dict[str, Any]] = field(default_factory=OrderedDict)
    engine_options_snapshot: dict[str, dict[str, Any]] = field(default_factory=dict)
    engine_info_snapshot: dict[str, dict[str, str]] = field(default_factory=dict)
    summary_snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    games_snapshot: dict[str, Any] | None = None
