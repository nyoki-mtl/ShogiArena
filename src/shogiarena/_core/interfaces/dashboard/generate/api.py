"""Generate mode API handlers for the arena dashboard."""

from __future__ import annotations

import json
from pathlib import Path

from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shogiarena._core.contexts.dashboard.ports.interface_dependencies import (
    DashboardRuntimeSupportPort,
    load_dashboard_interface_dependencies,
)
from shogiarena._core.interfaces.dashboard.generate.payloads import (
    GenerateSummary,
    RecordsManifest,
    RunState,
)
from shogiarena._core.interfaces.dashboard.http_response_builder import json_error_response
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int


class _GenerateGamesQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    limit: int = Field(default=50, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class GenerateAPI:
    """Lightweight API exposing generate-mode artifacts."""

    def __init__(
        self,
        *,
        db_path: Path,
        run_dir: Path,
        runtime_support: DashboardRuntimeSupportPort | None = None,
    ) -> None:
        self._db_path = db_path
        self._run_dir = run_dir
        dependencies = load_dashboard_interface_dependencies()
        self._runtime_support = runtime_support or dependencies.runtime_support

    def register_routes(self, app: web.Application) -> None:
        app.router.add_get("/api/generate/summary", self.get_summary)
        app.router.add_get("/api/generate/games", self.get_games)

    def _load_run_state(self) -> RunState:
        try:
            raw_mapping = self._runtime_support.load_run_state(self._run_dir)
            return RunState.model_validate(raw_mapping)
        except ValidationError:
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

    @staticmethod
    def _manifest_entry_int(entry: JsonObject, key: str, *, alias: str | None = None) -> int:
        raw_value = entry.get(key)
        if raw_value is None and alias is not None:
            raw_value = entry.get(alias)
        value = coerce_int(raw_value)
        if value is None:
            return 0
        return max(0, value)

    async def get_summary(self, _request: web.Request) -> web.Response:
        run_state = self._load_run_state()
        manifest = self._load_manifest(run_state)

        ro = run_state.config.records_output
        payload: GenerateSummary = {
            "totalGames": sum(self._manifest_entry_int(e, "games") for e in manifest.files),
            "totalPositions": sum(self._manifest_entry_int(e, "positions") for e in manifest.files),
            "totalBytes": sum(self._manifest_entry_int(e, "byte_count", alias="bytes") for e in manifest.files),
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
        try:
            parsed_query = _GenerateGamesQuery.model_validate(dict(request.rel_url.query))
        except ValidationError as exc:
            return json_error_response(str(exc), status=400, code="invalid_query")

        limit = parsed_query.limit
        offset = parsed_query.offset
        serialized: list[JsonObject] = []
        return web.json_response(
            {
                "games": serialized,
                "offset": offset,
                "limit": limit,
                "total": 0,
            }
        )
