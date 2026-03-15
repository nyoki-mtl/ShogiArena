"""Diagnostics handlers and utilities for Arena API server."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from aiohttp import web

from shogiarena._core.contexts.dashboard.application.live.diagnostics import (
    load_live_diagnostics_guidelines,
)
from shogiarena._core.contexts.dashboard.application.state_container import DashboardState
from shogiarena._core.interfaces.dashboard.ws_server import LiveWebSocketHub
from shogiarena._core.shared.kernel.json_types import JsonObject

logger = logging.getLogger(__name__)


class ArenaApiServerDiagnosticsMixin:
    run_dir: Path
    ws_hub: LiveWebSocketHub
    _state: DashboardState
    _diagnostics_retention_minutes: int
    _debug_last_get_worker: dict[int, float]

    def _copy_summary_snapshot(self, *, source: str = "tournament") -> JsonObject:
        raise NotImplementedError

    def _load_snapshot_retention_minutes(self) -> int:
        try:
            guidelines = load_live_diagnostics_guidelines(self.run_dir / "live_diagnostics.yml")
        except OSError as exc:
            logger.warning("Failed to load live_diagnostics.yml: %s", exc)
            return 0
        auto_snapshot = guidelines.get("autoSnapshot")
        if isinstance(auto_snapshot, Mapping):
            try:
                return max(0, int(auto_snapshot.get("retentionMinutes", 0)))
            except (TypeError, ValueError) as exc:
                logger.debug("Invalid autoSnapshot.retentionMinutes value %r: %s", auto_snapshot, exc)
                return 0
        return 0

    async def get_ws_diagnostics(self, _request: web.Request) -> web.Response:
        """Expose lightweight WebSocket hub diagnostics."""
        diagnostics = self.ws_hub.snapshot_diagnostics()
        return web.json_response(diagnostics)

    async def get_summary(self, request: web.Request) -> web.Response:
        source = request.query.get("source", "tournament").strip().lower() or "tournament"
        return web.json_response(self._copy_summary_snapshot(source=source))

    async def get_worker(self, request: web.Request) -> web.Response:
        start = time.perf_counter()
        idx = int(request.match_info["worker_idx"])
        last = self._debug_last_get_worker.get(idx)
        if last is not None:
            gap_ms = (start - last) * 1000.0
            if gap_ms <= 500.0:
                logger.debug("Rapid get_worker for %s (gap %.1f ms)", idx, gap_ms)
        self._debug_last_get_worker[idx] = start
        snap = self._state.get_worker_snapshot(idx)
        if not snap:
            return web.json_response(None)
        response = web.json_response(snap)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if elapsed_ms >= 50.0:
            print(f"[rest:get_worker] worker={idx} duration_ms={elapsed_ms:.1f}", flush=True)
        return response

    async def post_diagnostics_snapshot(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise web.HTTPBadRequest(text="Invalid JSON payload") from exc

        if not isinstance(payload, Mapping):
            raise web.HTTPBadRequest(text="Diagnostics snapshot payload must be a JSON object")

        snapshot = payload.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise web.HTTPBadRequest(text="'snapshot' field must be an object")

        timestamp = datetime.now(tz=UTC)
        metadata = {key: value for key, value in payload.items() if key != "snapshot"}
        metadata["receivedAt"] = timestamp.isoformat()
        record = {"snapshot": snapshot, "metadata": metadata}

        diagnostics_root = self._diagnostics_root()
        diagnostics_root.mkdir(parents=True, exist_ok=True)
        target_dir = diagnostics_root / timestamp.strftime("%Y%m%d")
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{timestamp.strftime('%H%M%S')}_{uuid4().hex[:6]}.json"
        file_path = target_dir / filename
        file_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

        self._prune_diagnostics_snapshots(diagnostics_root, self._diagnostics_retention_minutes)

        relative_path = file_path.relative_to(self.run_dir)
        response_payload = {"stored": str(relative_path), "receivedAt": metadata["receivedAt"]}
        return web.json_response(response_payload, status=201)

    def _diagnostics_root(self) -> Path:
        return (self.run_dir / "diagnostics").expanduser()

    def _prune_diagnostics_snapshots(self, root: Path, retention_minutes: int) -> None:
        if retention_minutes <= 0 or not root.exists():
            return
        cutoff = time.time() - (retention_minutes * 60)
        for path in root.rglob("*.json"):
            try:
                stat = path.stat()
            except OSError as exc:
                logger.debug("Skipping diagnostics snapshot stat for %s: %s", path, exc)
                continue
            if stat.st_mtime < cutoff:
                try:
                    path.unlink()
                except OSError as exc:
                    logger.debug("Failed to remove diagnostics snapshot %s: %s", path, exc)
                    continue
        for directory in sorted(root.rglob("*"), reverse=True):
            if not directory.is_dir():
                continue
            try:
                directory.rmdir()
            except OSError as exc:
                logger.debug("Skipping diagnostics directory prune for %s: %s", directory, exc)
                continue


__all__ = ["ArenaApiServerDiagnosticsMixin"]
