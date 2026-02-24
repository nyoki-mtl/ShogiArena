"""Game snapshot LRU cache for Arena Dashboard.

Manages per-game snapshots with LRU eviction to limit memory usage.
"""

from __future__ import annotations

import builtins
import logging
import os
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, cast

from shogiarena.utils.types.snapshots import GameSnapshot

if TYPE_CHECKING:
    from shogiarena.web.dashboard.backend.state_container import DashboardState

logger = logging.getLogger(__name__)


class GameSnapshotCache:
    """LRU cache for game snapshots.

    Manages game snapshots with automatic eviction based on cache size limits.
    Active games (those assigned to workers) are protected from eviction.

    Args:
        state: Shared dashboard state container.
        max_size: Maximum number of snapshots to cache. If None, uses environment
            variable or default value.
    """

    def __init__(
        self,
        state: DashboardState,
        max_size: int | None = None,
    ) -> None:
        self._state = state
        self._max_size = max_size if max_size is not None else self._load_max_size()

    @staticmethod
    def _load_max_size() -> int:
        """Load max cache size from environment variable."""
        raw = os.getenv("SHOGI_ARENA_DASHBOARD_MAX_GAME_SNAPSHOTS", "").strip()
        if not raw:
            return 512
        try:
            parsed = int(raw)
        except ValueError:
            logger.warning("Invalid SHOGI_ARENA_DASHBOARD_MAX_GAME_SNAPSHOTS=%s; using default", raw)
            return 512
        return max(0, parsed)

    @property
    def game_snapshots(self) -> OrderedDict[str, GameSnapshot]:
        """Access the underlying game snapshots OrderedDict."""
        return self._state.game_snapshots

    def active_game_ids(self) -> builtins.set[str]:
        """Return set of game IDs currently assigned to workers."""
        gids: builtins.set[str] = builtins.set()
        for value in self._state.worker_assignment.values():
            if isinstance(value, str) and value:
                gids.add(value)
        return gids

    def get(self, gid: str) -> GameSnapshot | None:
        """Get a game snapshot, updating its position in the LRU cache.

        Args:
            gid: Game ID to retrieve.

        Returns:
            The snapshot dict if found, None otherwise.
        """
        snap = self._state.game_snapshots.get(gid)
        if snap is None:
            return None
        self._state.game_snapshots.move_to_end(gid)
        return snap

    def set(self, gid: str, snapshot: dict[str, Any]) -> None:
        """Store a game snapshot, pruning old entries if needed.

        Args:
            gid: Game ID to store.
            snapshot: Snapshot data to store.
        """
        self._state.game_snapshots[gid] = cast(GameSnapshot, snapshot)
        self._state.game_snapshots.move_to_end(gid)
        self.prune()

    def prune(self) -> None:
        """Evict old snapshots to maintain cache size limit.

        Active games (assigned to workers) are protected from eviction
        unless the cache would exceed the maximum size.
        """
        max_size = self._max_size
        if max_size <= 0:
            return
        if len(self._state.game_snapshots) <= max_size:
            return

        active = self.active_game_ids()
        if len(active) > max_size:
            # Misconfiguration: more active games than cache size.
            # Evict oldest anyway to prevent OOM.
            logger.warning(
                "Active gid snapshots exceed cache size (active=%s > max=%s); evicting oldest",
                len(active),
                max_size,
            )
            while len(self._state.game_snapshots) > max_size:
                self._state.game_snapshots.popitem(last=False)
            return

        # Evict oldest non-active games
        scanned = 0
        while len(self._state.game_snapshots) > max_size and scanned < len(self._state.game_snapshots):
            oldest_gid = next(iter(self._state.game_snapshots))
            if oldest_gid in active:
                self._state.game_snapshots.move_to_end(oldest_gid)
                scanned += 1
                continue
            self._state.game_snapshots.popitem(last=False)

        # If still over limit, evict oldest regardless of active status
        while len(self._state.game_snapshots) > max_size:
            self._state.game_snapshots.popitem(last=False)

    def __len__(self) -> int:
        """Return number of cached snapshots."""
        return len(self._state.game_snapshots)

    def __contains__(self, gid: str) -> bool:
        """Check if a game ID is in the cache."""
        return gid in self._state.game_snapshots
