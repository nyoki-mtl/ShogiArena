"""Backend module for Arena Dashboard.

Provides modular components for the API server, organized into subpackages:

Root modules:
    - assets_writer: Dashboard HTML/CSS/JS asset generation
    - broadcast: WebSocket broadcast handler for live updates
    - game_cache: LRU cache for game snapshots
    - game_state: Game state management and snapshot construction
    - http_helpers: Utility functions for aiohttp responses
    - snapshot_storage: Storage and delta computation for dashboard snapshots
    - state_container: Shared state container for API handlers
    - static_handler: Static file serving with fallback support
    - ws_server: WebSocket hub for Live View updates

Subpackages:
    - spsa/: SPSA tuning functionality (API, services, streaming)
    - tournament/: Tournament API handlers
    - instances/: Instance management API
    - live/: Live view schema and diagnostics configuration
    - scheduler/: Match scheduling API
"""

from __future__ import annotations

# Utility modules
from shogiarena.web.dashboard.backend.assets_writer import (
    init_dashboard_html,
    read_dashboard_profiles_metadata,
    write_dashboard_assets,
)

# Core components
from shogiarena.web.dashboard.backend.broadcast import BroadcastHandler
from shogiarena.web.dashboard.backend.game_cache import GameSnapshotCache
from shogiarena.web.dashboard.backend.game_state import GameStateUpdater
from shogiarena.web.dashboard.backend.http_helpers import json_error_response
from shogiarena.web.dashboard.backend.snapshot_storage import SnapshotStorage
from shogiarena.web.dashboard.backend.state_container import DashboardState
from shogiarena.web.dashboard.backend.static_handler import StaticAssetsHandler
from shogiarena.web.dashboard.backend.ws_server import LiveWebSocketHub

__all__ = [
    # Core components
    "BroadcastHandler",
    "DashboardState",
    "GameSnapshotCache",
    "GameStateUpdater",
    "SnapshotStorage",
    "StaticAssetsHandler",
    # Utility modules
    "LiveWebSocketHub",
    "init_dashboard_html",
    "json_error_response",
    "read_dashboard_profiles_metadata",
    "write_dashboard_assets",
]
