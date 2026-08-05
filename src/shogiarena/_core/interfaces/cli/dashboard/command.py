"""Dashboard utilities for serving archived runs."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from shogiarena._core.contexts.dashboard.ports.archive_snapshot_ports import (
    GAME_DB_RELATIVE_PATH,
    ArchiveDatabaseSnapshotError,
)
from shogiarena._core.contexts.spsa.ports.ledger_ports import SPSA_LEDGER_RELATIVE_PATH
from shogiarena._core.interfaces.boundaries.parsers.dashboard import (
    detect_worker_count,
    infer_dashboard_profiles,
    load_spsa_config_for_dashboard,
    load_tournament_config_for_dashboard,
    pick_free_port,
)
from shogiarena._core.interfaces.cli.main import CliError
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.shared.kernel.paths import resolve_path_like

logger = logging.getLogger(__name__)

LEGACY_SPSA_DASHBOARD_MESSAGE = (
    "This SPSA archive uses the pre-1.2 JSON-only format and cannot be opened by ShogiArena 1.2.0. "
    "Use ShogiArena 1.1.0 to inspect this archive."
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "dashboard",
        help="Serve dashboard assets for a saved run",
        description="Serve the dashboard API for an existing run directory."
        " You can specify the run directory directly or provide the original config YAML.",
    )
    serve_parser = parser.add_subparsers(dest="dashboard_command")
    serve_parser.required = True

    serve_cmd = serve_parser.add_parser("serve", help="Serve dashboard data from an archived run")
    serve_cmd.add_argument(
        "--run-dir",
        help="Run directory containing game.db and data/*.js (defaults resolved from --config when omitted)",
    )
    serve_cmd.add_argument(
        "--config",
        help="Tournament/SPSA config YAML; used to resolve the expected run directory when --run-dir is absent",
    )
    serve_cmd.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Preferred HTTP port for the dashboard API (default: 8080)",
    )
    serve_cmd.set_defaults(async_handler=_serve_dashboard)


async def _serve_dashboard(args: argparse.Namespace) -> None:
    run_dir: Path | None = None
    num_workers: int | None = None

    if args.run_dir:
        resolved = resolve_path_like(args.run_dir)
        run_dir = Path(resolved)

    config_mode: str | None = None
    if args.config:
        config_path = Path(resolve_path_like(args.config))
        if not config_path.exists():
            raise CliError(f"configuration file not found: {config_path}")
        try:
            configured_run_dir, num_workers, profile = load_tournament_config_for_dashboard(
                config_path,
                run_dir_override=run_dir,
            )
            config_mode = profile
        except ValueError as tournament_error:
            try:
                configured_run_dir, num_workers = load_spsa_config_for_dashboard(
                    config_path,
                    original_error=tournament_error,
                    run_dir_override=run_dir,
                )
            except ValueError as spsa_error:
                raise CliError(str(spsa_error)) from spsa_error
            config_mode = "spsa"
        if run_dir is None:
            run_dir = configured_run_dir

    if run_dir is None:
        raise CliError("Specify either --run-dir or --config to locate the dashboard data")
    run_dir = run_dir.expanduser().resolve()
    if not run_dir.exists() or not run_dir.is_dir():
        raise CliError(f"run directory not found: {run_dir}")

    db_path = run_dir / "game.db"
    if not db_path.exists():
        raise CliError(f"game.db not found in {run_dir}")

    profiles = infer_dashboard_profiles(run_dir, config_mode)
    if "spsa" in profiles and not (run_dir / SPSA_LEDGER_RELATIVE_PATH).is_file():
        raise CliError(LEGACY_SPSA_DASHBOARD_MESSAGE)

    if num_workers is None or num_workers <= 0:
        detected = detect_worker_count(run_dir)
        if detected <= 0:
            raise CliError("Could not determine worker count; require data/workers or a config file")
        num_workers = detected

    requested_port = int(args.port or 8080)
    try:
        port = pick_free_port(requested_port)
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    if port != requested_port:
        logger.info("Port %s unavailable; using %s instead", requested_port, port)

    # checkpoint されていない WAL を持つアーカイブは、`immutable=1` で開くと commit 済みの
    # 行が無音で欠ける。原本を変えずに読める形へ解決してから配る(task 0066)。
    root = build_default_root()
    try:
        snapshot = root.archive_database_resolver(
            run_dir,
            relative_database_paths=[GAME_DB_RELATIVE_PATH, SPSA_LEDGER_RELATIVE_PATH],
        )
    except ArchiveDatabaseSnapshotError as exc:
        raise CliError(str(exc)) from exc

    try:
        server = root.api_server_factory(
            db_path=snapshot.database_path_for(GAME_DB_RELATIVE_PATH),
            port=port,
            run_dir=run_dir,
            instance_pool=None,
            read_only=True,
            ledger_run_dir=snapshot.database_dir_for(SPSA_LEDGER_RELATIVE_PATH),
            dashboard_num_workers=num_workers,
            dashboard_profiles=profiles,
        )
        # server を作った時点で projector が ledger を掴む。`start()` が失敗しても
        # `stop()` を必ず通さないと、snapshot の一時領域を掴んだままになる。
        try:
            await server.start()

            mode_label = config_mode or "dashboard"
            index_target = f"http://localhost:{port}/index.html"
            logger.info("%s dashboard available at %s", mode_label.capitalize(), index_target)

            try:
                while True:
                    await asyncio.sleep(3600)
            except (asyncio.CancelledError, KeyboardInterrupt):
                logger.info("Stopping dashboard server")
        finally:
            await server.stop()
    finally:
        snapshot.close()
