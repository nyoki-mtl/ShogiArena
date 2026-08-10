"""CSA watch API handlers for the arena dashboard."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from aiohttp import web

from shogiarena._core.contexts.csa_watch.application.live_snapshots import build_summary
from shogiarena._core.contexts.csa_watch.application.run_watcher import RunView
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue

logger = logging.getLogger(__name__)

RunViewsSupplier = Callable[[], tuple[RunView, ...]]
PersistenceSupplier = Callable[[], dict[str, JsonValue]]

# A wire log of a long session is large and nobody debugs it from byte 0, so the
# tail is bounded here rather than trusting the client to ask for a sane amount.
DEFAULT_WIRE_TAIL_BYTES = 512 * 1024
MAX_WIRE_TAIL_BYTES = 4 * 1024 * 1024


def _clamp_tail(raw: str | None) -> int:
    if raw is None:
        return DEFAULT_WIRE_TAIL_BYTES
    try:
        requested = int(raw)
    except ValueError:
        return DEFAULT_WIRE_TAIL_BYTES
    if requested <= 0:
        return DEFAULT_WIRE_TAIL_BYTES
    return min(requested, MAX_WIRE_TAIL_BYTES)


def _read_wire_tail(path: Path, tail: int) -> bytes:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > tail:
            handle.seek(size - tail)
            handle.readline()
        return handle.read()


class CsaAPI:
    """Serves the CSA profile's summary and folded run state.

    ``loadInitialDashboardData`` fetches the summary unconditionally and throws if
    it fails, so an empty log directory must still produce a valid answer rather
    than a 404. That is the day-one smoke test for this profile.
    """

    def __init__(
        self,
        *,
        views_supplier: RunViewsSupplier,
        run_dir: str | None = None,
        persistence_supplier: PersistenceSupplier | None = None,
    ) -> None:
        self._views_supplier = views_supplier
        self._run_dir = run_dir
        self._persistence_supplier = persistence_supplier

    def register_routes(self, app: web.Application) -> None:
        app.router.add_get("/api/csa/summary", self.get_summary)
        app.router.add_get("/api/csa/runs", self.get_runs)
        app.router.add_get("/api/csa/runs/{run_id}/wire", self.get_wire_log)

    def _summary(self) -> JsonObject:
        return build_summary(
            self._views_supplier(),
            timestamp=datetime.now(tz=UTC).isoformat(),
            run_dir=self._run_dir,
            persistence=self._persistence_supplier() if self._persistence_supplier is not None else None,
        )

    async def get_summary(self, _request: web.Request) -> web.Response:
        return web.json_response(self._summary())

    async def get_wire_log(self, request: web.Request) -> web.Response:
        """Serve the tail of one run's wire log.

        **Only ``{run_id}-wire.log`` is served.** It is the raw CSA protocol
        traffic — what 「CSA 生ログ」 means — and it is the only one of the three
        files the bridge audits: it masks the login password to ``*****`` as it
        writes.

        ``{run_id}-events.jsonl`` and ``{run_id}-engine.log`` are deliberately
        **not** served, and must not be added until someone audits them for
        secrets. Engine logs in particular echo USI option values, and users put
        anything in options. Nothing is lost by withholding the events log: the
        dashboard already renders everything in it.

        The client names a *run*, never a file. The path is derived from the
        matching ``RunView``, so no request can reach a file the watcher is not
        already reading.
        """
        run_id = request.match_info.get("run_id", "")
        view = next((candidate for candidate in self._views_supplier() if candidate.state.run_id == run_id), None)
        if view is None:
            raise web.HTTPNotFound(text=f"unknown run: {run_id}")

        wire_path = view.path.with_name(f"{view.state.run_id}-wire.log")
        tail = _clamp_tail(request.query.get("tail"))
        try:
            body = await asyncio.to_thread(_read_wire_tail, wire_path, tail)
        except (FileNotFoundError, NotADirectoryError):
            raise web.HTTPNotFound(text=f"no wire log for run: {run_id}") from None
        return web.Response(body=body, content_type="text/plain", charset="utf-8")

    async def get_runs(self, _request: web.Request) -> web.Response:
        payload: JsonObject = {
            "runs": [
                {
                    "run_id": view.state.run_id,
                    "worker_idx": view.worker_idx,
                    "phase": view.state.phase,
                    "path": str(view.path),
                    "games": len(view.state.games),
                }
                for view in self._views_supplier()
            ]
        }
        return web.json_response(payload)


__all__ = ["CsaAPI", "PersistenceSupplier", "RunViewsSupplier", "_read_wire_tail"]
