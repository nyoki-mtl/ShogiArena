"""Dashboard utilities for serving archived runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from shogiarena.arena.configs.spsa import load_config_yaml
from shogiarena.arena.configs.tournament import TournamentRunConfig
from shogiarena.cli.errors import CliError
from shogiarena.utils.common import project_dirs
from shogiarena.utils.common.paths import resolve_path_like
from shogiarena.utils.common.run_paths import latest_run_dir
from shogiarena.web.dashboard.api_server import ArenaAPIServer
from shogiarena.web.dashboard.backend.assets_writer import (
    DashboardProfile,
    read_dashboard_profiles_metadata,
    write_dashboard_assets,
)

logger = logging.getLogger(__name__)


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


def _pick_free_port(start: int, attempts: int = 20) -> int:
    for offset in range(attempts):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise CliError(f"failed to allocate a free port in range {start}-{start + attempts - 1}")


def _detect_worker_count(run_dir: Path) -> int:
    workers_dir = run_dir / "data" / "workers"
    if not workers_dir.exists():
        return 0
    indices: list[int] = []
    for worker_file in workers_dir.glob("worker_*.js"):
        stem = worker_file.stem
        try:
            idx = int(stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        indices.append(idx)
    if not indices:
        return 0
    return max(indices) + 1


def _detect_spsa_artifacts(run_dir: Path) -> bool:
    spsa_root = run_dir / "spsa"
    if not spsa_root.exists():
        return False
    markers = (
        spsa_root / "index.json",
        spsa_root / "events.jsonl",
        spsa_root / "meta.json",
        spsa_root / "ltc" / "results.jsonl",
    )
    return any(path.exists() for path in markers)


def _detect_run_state_profile(run_dir: Path) -> DashboardProfile | None:
    run_state_path = run_dir / "run_state.json"
    if not run_state_path.exists():
        return None
    try:
        run_state = json.loads(run_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(run_state, Mapping):
        return None
    config = run_state.get("config")
    if not isinstance(config, Mapping):
        return None
    sprt_conf = config.get("sprt")
    if isinstance(sprt_conf, Mapping):
        return "sprt"
    exp_name = config.get("experiment_name")
    if isinstance(exp_name, str):
        normalized = exp_name.strip().lower()
        if normalized == "match":
            return "match"
        if normalized == "generate":
            return "generate"
    return None


def _infer_dashboard_profiles(run_dir: Path, config_mode: str | None) -> tuple[DashboardProfile, ...]:
    if config_mode in {"tournament", "spsa", "match", "sprt", "generate"}:
        return (cast(DashboardProfile, config_mode),)

    metadata_profiles = read_dashboard_profiles_metadata(run_dir)
    if metadata_profiles:
        return metadata_profiles

    run_state_profile = _detect_run_state_profile(run_dir)
    if run_state_profile:
        return (run_state_profile,)

    if _detect_spsa_artifacts(run_dir):
        return ("spsa",)

    return ("tournament",)


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
            run_dir, num_workers, profile = _load_tournament_config(config_path)
            config_mode = profile
        except CliError as tournament_error:
            run_dir, num_workers = _load_spsa_config(config_path, tournament_error)
            config_mode = "spsa"

    if run_dir is None:
        raise CliError("Specify either --run-dir or --config to locate the dashboard data")
    run_dir = run_dir.expanduser().resolve()
    if not run_dir.exists() or not run_dir.is_dir():
        raise CliError(f"run directory not found: {run_dir}")

    db_path = run_dir / "game.db"
    if not db_path.exists():
        raise CliError(f"game.db not found in {run_dir}")

    if num_workers is None or num_workers <= 0:
        detected = _detect_worker_count(run_dir)
        if detected <= 0:
            raise CliError("Could not determine worker count; require data/workers or a config file")
        num_workers = detected

    profiles = _infer_dashboard_profiles(run_dir, config_mode)
    write_dashboard_assets(run_dir, num_workers, overwrite_data=False, profiles=profiles)

    requested_port = int(args.port or 8080)
    port = _pick_free_port(requested_port)
    if port != requested_port:
        logger.info("Port %s unavailable; using %s instead", requested_port, port)

    server = ArenaAPIServer(db_path=db_path, port=port, run_dir=run_dir, instance_pool=None)
    await server.start()

    port_js = run_dir / "data" / "arena_port.js"
    port_js.parent.mkdir(parents=True, exist_ok=True)
    port_js.write_text(f"window.ARENA_API_PORT = {port};\n", encoding="utf-8")

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


def _infer_profile_from_tournament_config(arena_cfg: TournamentRunConfig) -> DashboardProfile:
    if getattr(arena_cfg, "sprt", None) is not None:
        return "sprt"
    if getattr(arena_cfg, "generate", None) is not None:
        return "generate"
    exp_name = str(arena_cfg.experiment_name or "").strip().lower()
    if exp_name == "match":
        return "match"
    if exp_name == "generate":
        return "generate"
    return "tournament"


def _load_tournament_config(config_path: Path) -> tuple[Path, int, DashboardProfile]:
    try:
        arena_cfg = TournamentRunConfig.from_yaml(config_path)
    except Exception as exc:  # pragma: no cover - arena parsing raises richly
        raise CliError(f"failed to load tournament config: {config_path}") from exc

    num_parallel = arena_cfg.tournament.num_parallel
    if num_parallel is None or num_parallel <= 0:
        raise CliError("tournament config must define a positive tournament.num_parallel value")

    resolved = latest_run_dir(config_path, arena_cfg.output_dir / "tournament")
    if resolved is None:
        raise CliError("run directory not found; specify --run-dir")
    return resolved, int(num_parallel), _infer_profile_from_tournament_config(arena_cfg)


def _load_spsa_config(config_path: Path, original_error: CliError) -> tuple[Path, int]:
    try:
        spsa_cfg = load_config_yaml(config_path)
    except Exception as spsa_error:  # pragma: no cover - spsa loader raises dynamically
        raise CliError(f"failed to load config: {config_path}") from spsa_error

    num_workers = getattr(spsa_cfg, "num_workers", None)
    if num_workers is None:
        raise CliError("SPSA config must define num_workers")

    resolved = latest_run_dir(config_path, project_dirs.output_dir / "spsa")
    if resolved is None:
        raise CliError("run directory not found; specify --run-dir") from original_error
    return resolved, int(num_workers)
