"""Scheduler API handlers for exposing upcoming matches and reschedule controls."""

from __future__ import annotations

from json import JSONDecodeError
from typing import Any

from aiohttp import web
from aiohttp.client_exceptions import ContentTypeError
from aiohttp.web_exceptions import HTTPBadRequest

from shogiarena.web.dashboard.backend.http_helpers import json_error_response


class SchedulerAPIHandler:
    """Expose schedule inspection and reschedule triggers."""

    def __init__(self, runner: Any | None = None) -> None:
        self.runner = runner

    def setup_routes(self, app: web.Application) -> None:
        if self.runner is None:
            return
        app.router.add_get("/api/schedule", self.get_schedule)
        app.router.add_post("/api/schedule/reschedule", self.post_reschedule)
        app.router.add_post("/api/schedule/cancel", self.post_cancel)
        # Game-level adjustments for the dashboard controls
        app.router.add_post("/api/schedule/{game_id}/cancel", self.post_cancel_game)
        app.router.add_post("/api/schedule/{game_id}/restore", self.post_restore_game)
        app.router.add_post("/api/schedule/{game_id}/assign", self.post_assign_instance)

    async def get_schedule(self, request: web.Request) -> web.Response:
        runner = self.runner
        if runner is None:
            return json_error_response("scheduler not available", status=503, code="scheduler_unavailable")

        snapshot = await runner.get_schedule_snapshot()
        return web.json_response(snapshot)

    async def post_reschedule(self, request: web.Request) -> web.Response:
        runner = self.runner
        if runner is None:
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

        seed = payload.get("seed")
        if seed is not None and not isinstance(seed, int | str):
            return json_error_response("seed must be string or integer", status=400, code="invalid_seed")

        try:
            result = await runner.request_reschedule(seed=str(seed) if seed is not None else None)
        except RuntimeError as exc:
            return json_error_response(str(exc), status=409, code="reschedule_conflict")

        return web.json_response(result, status=202)

    async def post_restore_game(self, request: web.Request) -> web.Response:
        """Restore a previously cancelled game by id."""
        runner = self.runner
        if runner is None:
            return json_error_response("scheduler not available", status=503, code="scheduler_unavailable")

        game_id = request.match_info.get("game_id", "").strip()
        if not game_id:
            return json_error_response("game_id is required", status=400, code="missing_game_id")

        try:
            result = await runner.restore_game(game_id)
        except ValueError as exc:
            return json_error_response(str(exc), status=404, code="restore_not_found")
        except RuntimeError as exc:
            return json_error_response(str(exc), status=409, code="restore_conflict")

        return web.json_response(result, status=202)

    async def post_cancel(self, request: web.Request) -> web.Response:
        runner = self.runner
        if runner is None:
            return json_error_response("scheduler not available", status=503, code="scheduler_unavailable")

        result = await runner.cancel_pending_games()
        return web.json_response(result, status=202)

    async def post_cancel_game(self, request: web.Request) -> web.Response:
        """Cancel a single pending game by id."""
        runner = self.runner
        if runner is None:
            return json_error_response("scheduler not available", status=503, code="scheduler_unavailable")

        game_id = request.match_info.get("game_id", "").strip()
        if not game_id:
            return json_error_response("game_id is required", status=400, code="missing_game_id")

        try:
            result = await runner.cancel_game(game_id)
        except ValueError as exc:
            return json_error_response(str(exc), status=404, code="cancel_not_found")
        except RuntimeError as exc:
            return json_error_response(str(exc), status=409, code="cancel_conflict")

        return web.json_response(result, status=202)

    async def post_assign_instance(self, request: web.Request) -> web.Response:
        """Assign or clear a preferred instance for a pending game."""
        runner = self.runner
        if runner is None:
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

        raw_mode = payload.get("mode") if payload is not None else None
        if raw_mode is not None and not isinstance(raw_mode, str):
            return json_error_response("mode must be a string", status=400, code="invalid_mode")

        def _extract_optional_str(value: object, field: str) -> tuple[str | None, web.Response | None]:
            if value is None:
                return (None, None)
            if isinstance(value, str):
                return (value, None)
            return (
                None,
                json_error_response(f"{field} must be a string or null", status=400, code=f"invalid_{field}"),
            )

        shared_value = None
        if payload is not None:
            shared_value = payload.get("shared_instance")
            if shared_value is None and "instance_id" in payload:
                shared_value = payload.get("instance_id")
        shared_raw, shared_error = _extract_optional_str(shared_value, "shared_instance")
        if shared_error is not None:
            return shared_error
        black_value = payload.get("black_instance_id") if payload else None
        black_raw, black_error = _extract_optional_str(black_value, "black_instance_id")
        if black_error is not None:
            return black_error
        white_value = payload.get("white_instance_id") if payload else None
        white_raw, white_error = _extract_optional_str(white_value, "white_instance_id")
        if white_error is not None:
            return white_error

        def _sanitize(value: str | None) -> str | None:
            if value is None:
                return None
            stripped = value.strip()
            return stripped or None

        shared_normalized = _sanitize(shared_raw)
        black_normalized = _sanitize(black_raw)
        white_normalized = _sanitize(white_raw)

        require_install = bool(payload.get("require_install")) if isinstance(payload, dict) else False

        mode = str(raw_mode).strip().lower() if isinstance(raw_mode, str) else ""
        if not mode:
            if black_raw or white_raw:
                mode = "per_color"
            elif shared_raw:
                mode = "shared"
            else:
                mode = "auto"

        try:
            result = await runner.set_game_instance(
                game_id,
                mode=mode,
                shared_instance=shared_normalized,
                black_instance=black_normalized,
                white_instance=white_normalized,
                require_install=require_install,
            )
        except ValueError as exc:
            return json_error_response(str(exc), status=400, code="assign_invalid")
        except RuntimeError as exc:
            return json_error_response(str(exc), status=409, code="assign_conflict")

        return web.json_response(result, status=200)
