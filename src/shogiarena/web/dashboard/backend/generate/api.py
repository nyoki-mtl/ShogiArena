"""Generate mode API handlers for the arena dashboard."""

from __future__ import annotations

import json
from pathlib import Path

from aiohttp import web
from pydantic import ValidationError

from shogiarena.web.dashboard.backend.generate.types import (
    GenerateSummary,
    RecordsManifest,
    RunState,
)


class GenerateAPI:
    """Lightweight API exposing generate-mode artifacts."""

    def __init__(self, *, db_path: Path, run_dir: Path) -> None:
        self._db_path = db_path
        self._run_dir = run_dir

    def register_routes(self, app: web.Application) -> None:
        app.router.add_get("/api/generate/summary", self.get_summary)
        app.router.add_get("/api/generate/games", self.get_games)

    def _load_run_state(self) -> RunState:
        run_state_path = self._run_dir / "run_state.json"
        if not run_state_path.exists():
            return RunState()
        try:
            raw = json.loads(run_state_path.read_text(encoding="utf-8"))
            return RunState.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValidationError):
            return RunState()

    def _resolve_manifest_path(self, run_state: RunState) -> Path | None:
        output_dir = run_state.config.records_output.output_dir
        if not output_dir or not output_dir.strip():
            return None
        return Path(output_dir) / "records_manifest.json"

    def _load_manifest(self, run_state: RunState) -> RecordsManifest:
        manifest_path = self._resolve_manifest_path(run_state)
        if manifest_path is None or not manifest_path.exists():
            return RecordsManifest()
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            return RecordsManifest.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValidationError):
            return RecordsManifest()

    async def get_summary(self, request: web.Request) -> web.Response:
        run_state = self._load_run_state()
        manifest = self._load_manifest(run_state)

        ro = run_state.config.records_output
        payload: GenerateSummary = {
            "totalGames": sum(e.games for e in manifest.files),
            "totalPositions": sum(e.positions for e in manifest.files),
            "totalBytes": sum(e.byte_count for e in manifest.files),
            "fileCount": len(manifest.files),
            "runDir": str(self._run_dir),
            "tournamentType": "generate",
            "mode": "generate",
            "recordFormat": ro.format,
            "outputDir": ro.output_dir,
            "filePrefix": ro.file_prefix,
            "rules": run_state.config.rules,
        }
        return web.json_response(payload)

    async def get_games(self, request: web.Request) -> web.Response:
        limit_raw = request.rel_url.query.get("limit", "50")
        offset_raw = request.rel_url.query.get("offset", "0")

        try:
            limit = max(1, min(int(limit_raw), 1000))
        except ValueError:
            limit = 50
        try:
            offset = max(0, int(offset_raw))
        except ValueError:
            offset = 0
        serialized: list[dict[str, object]] = []
        return web.json_response(
            {
                "games": serialized,
                "offset": offset,
                "limit": limit,
                "total": 0,
            }
        )
