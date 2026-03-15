"""Scheduler API handlers for exposing upcoming matches and reschedule controls."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from json import JSONDecodeError

from aiohttp import web
from aiohttp.client_exceptions import ContentTypeError
from aiohttp.web_exceptions import HTTPBadRequest
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from shogiarena._core.contexts.dashboard.application.schedule_snapshot import (
    build_schedule_snapshot_from_games_snapshot,
)
from shogiarena._core.contexts.game_session.ports.dashboard_lifecycle_factory import (
    DashboardScheduleBoundaryPort,
)
from shogiarena._core.interfaces.dashboard.http_response_builder import json_error_response


class _RescheduleRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    seed: int | str | None = None


class _AssignInstanceRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: str | None = None
    shared_instance: str | None = None
    instance_id: str | None = None
    black_instance_id: str | None = None
    white_instance_id: str | None = None
    should_require_install: bool = False

    @field_validator(
        "mode",
        "shared_instance",
        "instance_id",
        "black_instance_id",
        "white_instance_id",
        mode="before",
    )
    @classmethod
    def _validate_optional_str(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("must be a string or null")
        return value


class SchedulerAPI:
    """Expose schedule inspection and reschedule triggers."""

    def __init__(
        self,
        boundary: DashboardScheduleBoundaryPort | None = None,
        *,
        games_snapshot_supplier: Callable[[], Mapping[str, object] | None] | None = None,
    ) -> None:
        self._boundary = boundary
        self._games_snapshot_supplier = games_snapshot_supplier

    def register_routes(self, app: web.Application) -> None:
        if self._boundary is None:
            return
        app.router.add_get("/api/schedule", self.get_schedule)
        app.router.add_post("/api/schedule/reschedule", self.post_reschedule)
        app.router.add_post("/api/schedule/cancel", self.post_cancel)
        # Game-level adjustments for the dashboard controls
        app.router.add_post("/api/schedule/{game_id}/cancel", self.post_cancel_game)
        app.router.add_post("/api/schedule/{game_id}/restore", self.post_restore_game)
        app.router.add_post("/api/schedule/{game_id}/assign", self.post_assign_instance)

    async def get_schedule(self, _request: web.Request) -> web.Response:
        boundary = self._boundary
        if boundary is None:
            return json_error_response("scheduler not available", status=503, code="scheduler_unavailable")

        if self._games_snapshot_supplier is not None:
            games_snapshot = self._games_snapshot_supplier()
            if isinstance(games_snapshot, Mapping):
                normalized_snapshot = build_schedule_snapshot_from_games_snapshot(games_snapshot)
                if normalized_snapshot is not None:
                    return web.json_response(normalized_snapshot)
        snapshot = await boundary.get_schedule_snapshot()
        return web.json_response(snapshot)

    async def post_reschedule(self, request: web.Request) -> web.Response:
        boundary = self._boundary
        if boundary is None:
            return json_error_response("scheduler not available", status=503, code="scheduler_unavailable")

        try:
            payload = await request.json()
        except (
            JSONDecodeError,
            ContentTypeError,
            HTTPBadRequest,
        ):  # pragma: no cover - aiohttp raises HTTPBadRequest internally
            payload = {}

        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return json_error_response("payload must be an object", status=400, code="invalid_payload")
        try:
            parsed = _RescheduleRequest.model_validate(payload)
        except ValidationError:
            return json_error_response("seed must be string or integer", status=400, code="invalid_seed")

        try:
            result = await boundary.request_reschedule(seed=str(parsed.seed) if parsed.seed is not None else None)
        except RuntimeError as exc:
            return json_error_response(str(exc), status=409, code="reschedule_conflict")

        return web.json_response(result, status=202)

    async def post_restore_game(self, request: web.Request) -> web.Response:
        """Restore a previously cancelled game by id."""
        boundary = self._boundary
        if boundary is None:
            return json_error_response("scheduler not available", status=503, code="scheduler_unavailable")

        game_id = request.match_info.get("game_id", "").strip()
        if not game_id:
            return json_error_response("game_id is required", status=400, code="missing_game_id")

        try:
            result = await boundary.restore_game(game_id)
        except ValueError as exc:
            return json_error_response(str(exc), status=404, code="restore_not_found")
        except RuntimeError as exc:
            return json_error_response(str(exc), status=409, code="restore_conflict")

        return web.json_response(result, status=202)

    async def post_cancel(self, _request: web.Request) -> web.Response:
        boundary = self._boundary
        if boundary is None:
            return json_error_response("scheduler not available", status=503, code="scheduler_unavailable")

        result = await boundary.cancel_pending_games()
        return web.json_response(result, status=202)

    async def post_cancel_game(self, request: web.Request) -> web.Response:
        """Cancel a single pending game by id."""
        boundary = self._boundary
        if boundary is None:
            return json_error_response("scheduler not available", status=503, code="scheduler_unavailable")

        game_id = request.match_info.get("game_id", "").strip()
        if not game_id:
            return json_error_response("game_id is required", status=400, code="missing_game_id")

        try:
            result = await boundary.cancel_game(game_id)
        except ValueError as exc:
            return json_error_response(str(exc), status=404, code="cancel_not_found")
        except RuntimeError as exc:
            return json_error_response(str(exc), status=409, code="cancel_conflict")

        return web.json_response(result, status=202)

    async def post_assign_instance(self, request: web.Request) -> web.Response:
        """Assign or clear a preferred instance for a pending game."""
        boundary = self._boundary
        if boundary is None:
            return json_error_response("scheduler not available", status=503, code="scheduler_unavailable")

        game_id = request.match_info.get("game_id", "").strip()
        if not game_id:
            return json_error_response("game_id is required", status=400, code="missing_game_id")

        try:
            payload = await request.json()
        except (JSONDecodeError, ContentTypeError, HTTPBadRequest):  # pragma: no cover
            payload = None

        if payload is not None and not isinstance(payload, dict):
            return json_error_response("payload must be an object", status=400, code="invalid_payload")
        payload_obj = payload if payload is not None else {}
        try:
            parsed = _AssignInstanceRequest.model_validate(payload_obj)
        except ValidationError as exc:
            for error in exc.errors():
                loc = error.get("loc")
                if not isinstance(loc, tuple) or not loc:
                    continue
                field = str(loc[0])
                if field == "mode":
                    return json_error_response("mode must be a string", status=400, code="invalid_mode")
                if field in {
                    "shared_instance",
                    "instance_id",
                    "black_instance_id",
                    "white_instance_id",
                }:
                    if field == "instance_id":
                        field = "shared_instance"
                    return json_error_response(
                        f"{field} must be a string or null",
                        status=400,
                        code=f"invalid_{field}",
                    )
            return json_error_response("payload must be an object", status=400, code="invalid_payload")

        def _sanitize(value: str | None) -> str | None:
            if value is None:
                return None
            stripped = value.strip()
            if not stripped:
                return None
            return stripped

        shared_raw = parsed.shared_instance
        if shared_raw is None and "instance_id" in payload_obj:
            shared_raw = parsed.instance_id
        black_raw = parsed.black_instance_id
        white_raw = parsed.white_instance_id

        shared_normalized = _sanitize(shared_raw)
        black_normalized = _sanitize(black_raw)
        white_normalized = _sanitize(white_raw)

        mode = parsed.mode.strip().lower() if isinstance(parsed.mode, str) else ""
        if not mode:
            if black_raw or white_raw:
                mode = "per_color"
            elif shared_raw:
                mode = "shared"
            else:
                mode = "auto"

        try:
            result = await boundary.set_game_instance(
                game_id,
                mode=mode,
                shared_instance=shared_normalized,
                black_instance=black_normalized,
                white_instance=white_normalized,
                should_require_install=bool(parsed.should_require_install),
            )
        except ValueError as exc:
            return json_error_response(str(exc), status=400, code="assign_invalid")
        except RuntimeError as exc:
            return json_error_response(str(exc), status=409, code="assign_conflict")

        return web.json_response(result, status=200)
