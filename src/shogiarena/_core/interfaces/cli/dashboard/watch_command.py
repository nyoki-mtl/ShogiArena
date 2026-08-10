"""``shogiarena dashboard watch`` — serve a live dashboard for CSA bridge runs.

This is a sibling of ``serve``, not a flag on it. ``serve``'s contract is "an
archive nobody is writing to", enforced by a liveness guard; watching a CSA run
is the exact opposite. Putting both postures behind one command would turn the
difference into a swamp of flags (``0068`` decision 1).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from pathlib import Path

from shogiarena._core.contexts.csa_watch.application.game_persistence import CsaGamePersister
from shogiarena._core.contexts.csa_watch.application.live_publisher import CsaLivePublisher
from shogiarena._core.contexts.csa_watch.application.run_watcher import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    CsaRunWatcher,
)
from shogiarena._core.interfaces.boundaries.parsers.dashboard import pick_free_port
from shogiarena._core.interfaces.cli.main import CliArgumentError, CliError
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.interfaces.dashboard.api_server.server import ArenaAPIServer
from shogiarena._core.interfaces.dashboard.csa.api import CsaAPI
from shogiarena._core.platform.settings import project_dirs

logger = logging.getLogger(__name__)

CSA_OUTPUT_SUBDIR = "csa"
GAME_DB_NAME = "game.db"
DASHBOARD_ASSET_SUBDIR = "dashboard"
MIN_POLL_INTERVAL_SECONDS = 0.2


def register_watch(dashboard_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = dashboard_sub.add_parser(
        "watch",
        help="Serve a live dashboard for CSA bridge runs still being written",
        description="Tail {run_id}-events.jsonl files and stream the games to the live dashboard.",
    )
    parser.add_argument(
        "--csa-log-dir",
        required=True,
        type=Path,
        help="Directory the bridge writes {run_id}-events.jsonl into",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Preferred HTTP port for the dashboard API (default: 8080)",
    )
    parser.add_argument(
        "--out-run-dir",
        type=Path,
        help="Directory for dashboard assets and finished games (default: <output>/csa/<timestamp>)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help=f"Poll interval in seconds (default: {DEFAULT_POLL_INTERVAL_SECONDS})",
    )
    parser.set_defaults(async_handler=watch_csa_dashboard)


def _resolve_log_dir(raw: Path) -> Path:
    log_dir = raw.expanduser()
    if not log_dir.is_dir():
        raise CliArgumentError(f"csa log directory not found: {log_dir}")
    return log_dir


def _resolve_out_run_dir(raw: Path | None, log_dir: Path) -> Path:
    if raw is None:
        out_run_dir = project_dirs.output_dir / CSA_OUTPUT_SUBDIR / time.strftime("%Y%m%d_%H%M%S")
    else:
        out_run_dir = raw.expanduser()
    if out_run_dir.resolve() == log_dir.resolve():
        raise CliArgumentError("--out-run-dir must not be the CSA log directory; keep artifacts away from evidence")
    out_run_dir.mkdir(parents=True, exist_ok=True)
    return out_run_dir


def _write_dashboard_api_port(dashboard_dir: Path, port: int) -> None:
    port_js = dashboard_dir / "data" / "arena_port.js"
    port_js.write_text(f"window.ARENA_API_PORT = {port};\n", encoding="utf-8")


async def watch_csa_dashboard(args: argparse.Namespace) -> None:
    log_dir = _resolve_log_dir(args.csa_log_dir)
    out_run_dir = _resolve_out_run_dir(args.out_run_dir, log_dir)
    interval = max(MIN_POLL_INTERVAL_SECONDS, float(args.interval or DEFAULT_POLL_INTERVAL_SECONDS))

    root = build_default_root()
    watcher = CsaRunWatcher(root.csa_log_source_factory(log_dir))
    try:
        await watcher.refresh()
        views = watcher.views()
        # An empty log directory is a normal state: floodgate pairs on the hour and
        # the operator opens the page before the bridge has written anything.
        num_workers = max(1, len(views))

        db_path = out_run_dir / GAME_DB_NAME
        requested_port = int(args.port or 8080)
        try:
            port = pick_free_port(requested_port)
        except ValueError as exc:
            raise CliError(str(exc)) from exc
        if port != requested_port:
            logger.info("Port %s unavailable; using %s instead", requested_port, port)

        # Assets go under `<run_dir>/dashboard`, the same layout a live tournament
        # writes, so `dashboard serve --run-dir <out>` can reopen this directory
        # once the games in it have finished.
        # Baked with no worker boards on purpose. A tournament knows its workers
        # up front; a watched log directory holds every run it has ever seen, so
        # one board per run fills Live View with finished games and pushes the
        # running one past the board limit. The page opens boards from the CSA
        # summary instead, for the runs that are still running.
        dashboard_dir = out_run_dir / DASHBOARD_ASSET_SUBDIR
        root.init_dashboard_html(dashboard_dir, 0, profiles=("csa",))
        _write_dashboard_api_port(dashboard_dir, port)

        # The database handle is opened last, after everything that can still
        # refuse to start: an early failure must not leave a SQLite connection open.
        persister = CsaGamePersister(root.csa_record_store_factory(db_path), root.csa_board_replay)
        try:
            server = root.api_server_factory(
                db_path=db_path,
                port=port,
                run_dir=out_run_dir,
                instance_pool=None,
                read_only=False,
                dashboard_num_workers=num_workers,
                dashboard_profiles=("csa",),
            )
            # The shared factory port is deliberately minimal; the CSA routes and
            # the live-stream methods live on the concrete server.
            if not isinstance(server, ArenaAPIServer):
                raise CliError("dashboard API server factory did not produce an ArenaAPIServer")
            CsaAPI(
                views_supplier=watcher.views,
                run_dir=str(out_run_dir),
                persistence_supplier=persister.snapshot,
            ).register_routes(server.app)

            publisher = CsaLivePublisher(
                server,
                root.csa_board_replay,
                run_dir=str(out_run_dir),
                persistence_supplier=persister.snapshot,
            )
            try:
                await server.start()
                logger.info("CSA dashboard available at http://localhost:%s/index.html", port)
                logger.info("Watching %s; artifacts under %s", log_dir, out_run_dir)
                # The JSONL projection is live authority from the first frame,
                # too. A pre-existing finished game may make SQLite block, but
                # an active game in the same directory must already be visible.
                publisher.publish(views, views)
                if not views:
                    publisher.publish_run_views(views)
                previous_revision = persister.revision
                await asyncio.to_thread(persister.persist_finished, views)
                if persister.revision != previous_revision:
                    publisher.publish_run_views(views)
                await _follow(watcher, publisher, persister, interval)
            finally:
                await server.stop()
        finally:
            persister.close()
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Stopping CSA dashboard")
    finally:
        watcher.close()


async def _follow(
    watcher: CsaRunWatcher,
    publisher: CsaLivePublisher,
    persister: CsaGamePersister,
    interval: float,
) -> None:
    while True:
        await asyncio.sleep(interval)
        changed = await watcher.refresh()
        views = watcher.views()
        if changed:
            # The JSONL projection is the live authority. A slow or locked
            # derived database must not delay the terminal board update.
            publisher.publish(changed, views)
        # A game whose store write failed is deliberately not marked persisted so
        # it *can* be retried — but its run has finished and will never appear in
        # `changed` again, so passing only `changed` meant it never was. When
        # something is waiting, sweep every run instead; the persisted set makes
        # that one set lookup per game.
        targets = watcher.views() if persister.has_deferred else changed
        if not targets and not changed:
            continue
        # Replay and the database write are both blocking; keeping them off the
        # loop keeps the dashboard responsive while a game is being sealed.
        previous_revision = persister.revision
        if targets:
            await asyncio.to_thread(persister.persist_finished, targets)
        if persister.revision != previous_revision:
            publisher.publish_run_views(views)


__all__ = ["register_watch", "watch_csa_dashboard"]
