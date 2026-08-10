"""Boundary parsers for dashboard CLI arguments and metadata resolution."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, ValidationError

from shogiarena._core.contexts.tournament.application.entrypoints import build_tournament_run_config
from shogiarena._core.interfaces.cli.config_file_loaders import parse_spsa_config_file, parse_tournament_config_file
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.interfaces.dashboard.assets_writer import DashboardProfile, read_dashboard_profiles_metadata
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.run_paths import latest_run_dir, latest_run_dir_for_key
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_optional_text

_VALID_DASHBOARD_PROFILES: set[str] = {"tournament", "spsa", "match", "sprt", "generate", "csa"}
logger = logging.getLogger(__name__)


class _DashboardRunStateConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sprt: object | None = None
    experiment_name: str | None = None


class _DashboardRunState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    config: _DashboardRunStateConfig | None = None


class _TournamentConfigSectionPort(Protocol):
    num_parallel: int | None


class _TournamentDashboardConfigPort(Protocol):
    sprt: object | None
    generate: object | None
    experiment_name: str | None
    tournament: _TournamentConfigSectionPort
    output_dir: Path

    def get_schedule_hash(self) -> str: ...


def pick_free_port(start: int, attempts: int = 20) -> int:
    import socket

    for offset in range(attempts):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise ValueError(f"failed to allocate a free port in range {start}-{start + attempts - 1}")


def detect_worker_count(run_dir: Path) -> int:
    workers_dir = run_dir / "dashboard" / "data" / "workers"
    if workers_dir.exists():
        indices: list[int] = []
        for worker_file in workers_dir.glob("worker_*.js"):
            stem = worker_file.stem
            parts = stem.split("_", maxsplit=1)
            idx = coerce_int(parts[1]) if len(parts) > 1 else None
            if idx is None:
                continue
            indices.append(idx)
        if indices:
            return max(indices) + 1

    manifest_path = run_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(manifest, Mapping):
        return 0
    for section_name in ("tournament", "spsa"):
        section = manifest.get(section_name)
        if not isinstance(section, Mapping):
            continue
        num_parallel = coerce_int(section.get("num_parallel"))
        if num_parallel is not None and num_parallel > 0:
            return num_parallel
    return 0


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


def infer_run_state_profile(run_dir: Path) -> DashboardProfile | None:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return None

    try:
        manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        logger.debug("Failed to infer manifest profile from %s: %s", manifest_path, exc)
        return None

    if not isinstance(manifest_raw, Mapping):
        return None
    schedule = manifest_raw.get("schedule")
    if isinstance(schedule, Mapping) and schedule.get("kind") == "generate":
        return "generate"
    if manifest_raw.get("sprt") is not None:
        return "sprt"
    exp_name = manifest_raw.get("experiment_name")
    if isinstance(exp_name, str):
        normalized = exp_name.strip().lower()
        if normalized == "match":
            return "match"
    return None


def _normalize_profile(profile: str | None) -> DashboardProfile:
    if profile == "tournament":
        return "tournament"
    if profile == "spsa":
        return "spsa"
    if profile == "match":
        return "match"
    if profile == "sprt":
        return "sprt"
    if profile == "generate":
        return "generate"
    if profile == "csa":
        return "csa"
    return "tournament"


def infer_dashboard_profiles(run_dir: Path, config_mode: str | None) -> tuple[DashboardProfile, ...]:
    if config_mode in _VALID_DASHBOARD_PROFILES:
        return (_normalize_profile(config_mode),)

    metadata_profiles = read_dashboard_profiles_metadata(run_dir)
    if metadata_profiles:
        return metadata_profiles

    run_state_profile = infer_run_state_profile(run_dir)
    if run_state_profile is not None:
        return (run_state_profile,)

    if _detect_spsa_artifacts(run_dir):
        return ("spsa",)

    return ("tournament",)


def _infer_profile_from_tournament_config(arena_cfg: _TournamentDashboardConfigPort) -> DashboardProfile:
    if arena_cfg.sprt is not None:
        return "sprt"
    if arena_cfg.generate is not None:
        return "generate"

    exp_name = (coerce_optional_text(arena_cfg.experiment_name) or "").lower()
    if exp_name == "match":
        return "match"
    if exp_name == "generate":
        return "generate"
    return "tournament"


def load_tournament_config_for_dashboard(
    config_path: Path,
    *,
    run_dir_override: Path | None = None,
) -> tuple[Path, int, DashboardProfile]:
    try:
        tournament_payload = parse_tournament_config_file(config_path)
        arena_cfg_raw = build_tournament_run_config(
            tournament_payload,
            base_dir=config_path.parent,
            source_path=config_path,
            runtime=build_default_root().tournament_runtime,
        )
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        raise ValueError(f"failed to load tournament config: {config_path}") from exc

    arena_cfg = cast(_TournamentDashboardConfigPort, arena_cfg_raw)
    num_parallel = arena_cfg.tournament.num_parallel
    if num_parallel is None or num_parallel <= 0:
        raise ValueError("tournament config must define a positive tournament.num_parallel value")

    profile = _infer_profile_from_tournament_config(arena_cfg)
    output_kind = "generate" if profile == "generate" else "tournament"
    resolved = run_dir_override or latest_run_dir_for_key(
        arena_cfg.output_dir / output_kind,
        config_path.stem,
        arena_cfg.get_schedule_hash(),
    )
    if resolved is None:
        raise ValueError("run directory not found; specify --run-dir")
    parsed_num_parallel = coerce_int(num_parallel)
    if parsed_num_parallel is None or parsed_num_parallel <= 0:
        raise ValueError("tournament config must define a positive tournament.num_parallel value")
    return resolved, parsed_num_parallel, profile


def load_spsa_config_for_dashboard(
    config_path: Path,
    *,
    original_error: Exception | None = None,
    run_dir_override: Path | None = None,
) -> tuple[Path, int]:
    try:
        payload = parse_spsa_config_file(config_path)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        raise ValueError(f"failed to load config: {config_path}") from exc

    raw_spsa = payload.get("spsa")
    if not isinstance(raw_spsa, Mapping):
        raise ValueError("SPSA config must define spsa mapping")

    spsa_map = {str(key): value for key, value in raw_spsa.items()}
    num_workers = 4
    parsed_num_parallel = coerce_int(spsa_map.get("num_parallel"))
    if parsed_num_parallel is not None:
        num_workers = parsed_num_parallel
    if num_workers <= 0:
        raise ValueError("SPSA config must define positive spsa.num_parallel")

    resolved = run_dir_override or latest_run_dir(config_path, project_dirs.output_dir / "spsa")
    if resolved is None:
        raise ValueError("run directory not found; specify --run-dir") from original_error
    return resolved, num_workers


__all__ = [
    "DashboardProfile",
    "detect_worker_count",
    "infer_dashboard_profiles",
    "infer_run_state_profile",
    "load_spsa_config_for_dashboard",
    "load_tournament_config_for_dashboard",
    "pick_free_port",
]
