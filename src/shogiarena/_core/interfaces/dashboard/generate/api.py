"""Generate mode API handlers for the arena dashboard."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shogiarena._core.contexts.dashboard.ports.interface_dependencies import (
    DashboardGameQueryPort,
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

logger = logging.getLogger(__name__)


class GenerateArtifactError(Exception):
    """Raised when a generate-run artifact exists but is corrupt or schema-invalid.

    Distinguished from a *missing* optional artifact (which is treated as empty): a present but
    unreadable/unparseable/invalid artifact must surface rather than be silently zeroed.
    """


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
        game_query: DashboardGameQueryPort | None = None,
    ) -> None:
        self._db_path = db_path
        self._run_dir = run_dir
        # game_query is resolved lazily in get_games so that callers needing only the summary
        # (e.g. tests that inject just runtime_support) do not require configured dependencies.
        self._game_query = game_query
        if runtime_support is None:
            dependencies = load_dashboard_interface_dependencies()
            self._runtime_support = dependencies.runtime_support
        else:
            self._runtime_support = runtime_support

    def register_routes(self, app: web.Application) -> None:
        app.router.add_get("/api/generate/summary", self.get_summary)
        app.router.add_get("/api/generate/games", self.get_games)

    def _load_run_state(self) -> RunState:
        # A missing state.json yields a fresh run; a present-but-corrupt or schema-invalid state
        # must surface rather than silently default to RunState(). Read strictly here (mirroring
        # _load_run_manifest) instead of via the shared lenient loader, which collapses malformed
        # JSON to {} for the graceful pairwise/sprt dashboards.
        state_path = self._run_dir / "state.json"
        try:
            text = state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return RunState()
        except OSError as exc:
            raise GenerateArtifactError(f"state.json is unreadable: {exc}") from exc
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GenerateArtifactError(f"state.json is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise GenerateArtifactError("state.json must be a JSON object")
        try:
            return RunState.model_validate(raw)
        except ValidationError as exc:
            raise GenerateArtifactError(f"state.json is schema-invalid: {exc}") from exc

    def _load_run_manifest(self) -> JsonObject:
        manifest_path = self._run_dir / "manifest.json"
        try:
            text = manifest_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise GenerateArtifactError(f"manifest.json is unreadable: {exc}") from exc
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GenerateArtifactError(f"manifest.json is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise GenerateArtifactError("manifest.json must be a JSON object")
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
        # The file exists here, so any read/parse/validate failure means it is corrupt and must
        # surface rather than collapse to an empty (zero-count) manifest.
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            return RecordsManifest.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise GenerateArtifactError(f"records_manifest.json is corrupt: {exc}") from exc

    @staticmethod
    def _manifest_entry_int(entry: JsonObject, key: str) -> int:
        value = coerce_int(entry.get(key))
        if value is None:
            return 0
        return max(0, value)

    async def get_summary(self, _request: web.Request) -> web.Response:
        try:
            run_state = self._load_run_state()
            run_manifest = self._load_run_manifest()
            records_manifest = self._load_manifest(run_state, run_manifest)
        except GenerateArtifactError as exc:
            logger.error("Generate summary failed: corrupt run artifact: %s", exc, exc_info=exc)
            return json_error_response(str(exc), status=500, code="corrupt_run_artifact")

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
            "total_games": sum(self._manifest_entry_int(e, "games") for e in records_manifest.files),
            "total_positions": sum(self._manifest_entry_int(e, "positions") for e in records_manifest.files),
            "total_bytes": sum(self._manifest_entry_int(e, "bytes") for e in records_manifest.files),
            "file_count": len(records_manifest.files),
            "run_dir": str(self._run_dir),
            "tournament_type": "generate",
            "mode": "generate",
            "record_format": coerce_optional_text(manifest_records.get("format")) if manifest_records else ro.format,
            "output_dir": coerce_optional_text(manifest_records.get("output_dir"))
            if manifest_records
            else ro.output_dir,
            "file_prefix": coerce_optional_text(manifest_records.get("file_prefix"))
            if manifest_records
            else ro.file_prefix,
            "rules": rules,
            "run_status": manifest_status,
            "is_resumable": is_resumable_manifest(run_manifest),
        }
        return web.json_response(payload)

    async def get_games(self, request: web.Request) -> web.Response:
        try:
            parsed_query = _GenerateGamesQuery.model_validate(dict(request.rel_url.query))
        except ValidationError as exc:
            return json_error_response(str(exc), status=400, code="invalid_query")

        try:
            game_query = self._game_query or load_dashboard_interface_dependencies().game_query
            # The generate run's database holds only its own self-play games, so the standard games
            # payload builder returns exactly the generated games (no game_type filter needed).
            payload = game_query.build_games_raw_payload(
                self._db_path,
                limit=parsed_query.limit,
                offset=parsed_query.offset,
                search_query=None,
            )
        except Exception as exc:
            # Convert DB-layer failures (e.g. a corrupt game.db) and missing-dependency errors to a
            # clean 500 at the API boundary instead of leaking an unhandled traceback.
            logger.exception("Failed to list generate games")
            return json_error_response(str(exc), status=500, code="games_query_failed")
        return web.json_response(payload)
