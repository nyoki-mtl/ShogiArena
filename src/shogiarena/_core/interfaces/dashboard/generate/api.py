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
from shogiarena._core.shared.kernel.run_manifest_reader import is_resumable_manifest
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_optional_text
from shogiarena._core.shared.kernel.serialization import json_serialize


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
        if runtime_support is None:
            dependencies = load_dashboard_interface_dependencies()
            self._runtime_support = dependencies.runtime_support
        else:
            self._runtime_support = runtime_support

    def register_routes(self, app: web.Application) -> None:
        app.router.add_get("/api/generate/summary", self.get_summary)
        app.router.add_get("/api/generate/games", self.get_games)

    def _load_run_state(self) -> RunState:
        try:
            raw_mapping = self._runtime_support.load_run_state(self._run_dir)
            return RunState.model_validate(raw_mapping)
        except ValidationError:
            return RunState()

    def _load_run_manifest(self) -> JsonObject:
        manifest_path = self._run_dir / "manifest.json"
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(key): json_serialize(value) for key, value in raw.items()}

    @staticmethod
    def _manifest_records_output(manifest: JsonObject) -> JsonObject:
        raw = manifest.get("records_output")
        if not isinstance(raw, dict):
            return {}
        return {str(key): json_serialize(value) for key, value in raw.items()}

    def _resolve_manifest_path(self, run_state: RunState, run_manifest: JsonObject) -> Path | None:
        manifest_records = self._manifest_records_output(run_manifest)
        output_dir = manifest_records.get("output_dir") or run_state.config.records_output.output_dir
        if not isinstance(output_dir, str):
            return None
        if not output_dir or not output_dir.strip():
            return None
        return Path(output_dir) / "records_manifest.json"

    def _load_manifest(self, run_state: RunState, run_manifest: JsonObject) -> RecordsManifest:
        manifest_path = self._resolve_manifest_path(run_state, run_manifest)
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
        run_manifest = self._load_run_manifest()
        records_manifest = self._load_manifest(run_state, run_manifest)

        ro = run_state.config.records_output
        manifest_records = self._manifest_records_output(run_manifest)
        rules_raw = run_manifest.get("rules")
        rules = (
            {str(key): json_serialize(value) for key, value in rules_raw.items()}
            if isinstance(rules_raw, dict)
            else run_state.config.rules
        )
        manifest_status = coerce_optional_text(run_manifest.get("status"))
        payload: GenerateSummary = {
            "totalGames": sum(self._manifest_entry_int(e, "games") for e in records_manifest.files),
            "totalPositions": sum(self._manifest_entry_int(e, "positions") for e in records_manifest.files),
            "totalBytes": sum(self._manifest_entry_int(e, "byte_count", alias="bytes") for e in records_manifest.files),
            "fileCount": len(records_manifest.files),
            "runDir": str(self._run_dir),
            "tournamentType": "generate",
            "mode": "generate",
            "recordFormat": coerce_optional_text(manifest_records.get("format")) if manifest_records else ro.format,
            "outputDir": coerce_optional_text(manifest_records.get("output_dir"))
            if manifest_records
            else ro.output_dir,
            "filePrefix": coerce_optional_text(manifest_records.get("file_prefix"))
            if manifest_records
            else ro.file_prefix,
            "rules": rules,
            "runStatus": manifest_status,
            "isResumable": is_resumable_manifest(run_manifest),
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
