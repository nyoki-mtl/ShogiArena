"""OpenBench create-test payload builder."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from shogiarena._core.contexts.game_session.adapters.openbench.client_types import (
    parse_openbench_float_pair,
)
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.settings_loading.settings_models import RepoSettings
from shogiarena._core.shared.kernel.time_control import TimeControlLimits
from shogiarena._core.shared.kernel.time_control_resolution import build_time_control_limits

from .fetch_payload import fetch_books, fetch_engine_nps
from .scalar_validation import _require_int, _require_positive_int

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenBenchCreateEngineSpec:
    artifact: str | None
    time_control: TimeControlLimits | None


@dataclass(frozen=True)
class OpenBenchCreateSprtSpec:
    elo0: float
    elo1: float
    alpha: float
    beta: float
    max_games: int | None


@dataclass(frozen=True)
class OpenBenchCreatePayloadInput:
    dev_engine: str
    base_engine: str
    dev_repo: str
    base_repo: str
    dev_branch: str
    base_branch: str
    dev_options: str
    base_options: str
    dev_time_control: str
    base_time_control: str
    book_name: str
    scale_nps: str | int
    test_mode: str
    test_bounds: str
    test_confidence: str
    test_max_games: str | int
    throughput: int
    workload_size: int
    upload_pgns: str
    scale_method: str
    dev_bench: str
    base_bench: str
    dev_network: str
    base_network: str
    priority: int
    syzygy_wdl: str
    syzygy_adj: str
    win_adj: str
    draw_adj: str


@dataclass(frozen=True)
class OpenBenchCreatePayloadRequest:
    payload: OpenBenchCreatePayloadInput
    dev_spec: OpenBenchCreateEngineSpec
    base_spec: OpenBenchCreateEngineSpec
    base_time_control: TimeControlLimits | None
    sprt: OpenBenchCreateSprtSpec | None


def build_openbench_create_payload(
    request: OpenBenchCreatePayloadRequest,
    *,
    openbench_server: str,
    openbench_username: str,
    openbench_password: str,
) -> dict[str, str]:
    payload_cfg = request.payload

    dev_engine = (payload_cfg.dev_engine or "").strip()
    base_engine = (payload_cfg.base_engine or "").strip()
    if not dev_engine or not base_engine:
        raise ValueError("openbench.create.payload.dev_engine/base_engine are required")

    dev_spec = request.dev_spec
    base_spec = request.base_spec

    dev_repo = _resolve_repo_url(dev_spec, payload_cfg.dev_repo or "")
    base_repo = _resolve_repo_url(base_spec, payload_cfg.base_repo or "")
    dev_branch = _resolve_branch(dev_spec, payload_cfg.dev_branch or "")
    base_branch = _resolve_branch(base_spec, payload_cfg.base_branch or "")

    dev_tc = _resolve_time_control(
        payload_cfg.dev_time_control or "",
        base_tc=request.base_time_control,
        engine_tc=dev_spec.time_control,
    )
    base_tc = _resolve_time_control(
        payload_cfg.base_time_control or "",
        base_tc=request.base_time_control,
        engine_tc=base_spec.time_control,
    )

    sprt_cfg = request.sprt
    test_mode = payload_cfg.test_mode.strip().upper()
    if test_mode not in {"SPRT", "GAMES"}:
        raise ValueError("openbench.create.payload.test_mode must be SPRT or GAMES")
    test_bounds = payload_cfg.test_bounds.strip()
    if test_bounds == "auto":
        if sprt_cfg is None:
            raise ValueError("openbench.create.payload.test_bounds=auto requires sprt config")
        test_bounds = f"[{sprt_cfg.elo0}, {sprt_cfg.elo1}]"
    test_conf = payload_cfg.test_confidence.strip()
    if test_conf == "auto":
        if sprt_cfg is None:
            raise ValueError("openbench.create.payload.test_confidence=auto requires sprt config")
        test_conf = f"[{sprt_cfg.beta}, {sprt_cfg.alpha}]"
    if test_mode == "SPRT":
        lower_elo, upper_elo = parse_openbench_float_pair(
            test_bounds,
            field_name="openbench.create.payload.test_bounds",
        )
        if lower_elo >= upper_elo:
            raise ValueError("openbench.create.payload.test_bounds must satisfy lower < upper")
        beta_conf, alpha_conf = parse_openbench_float_pair(
            test_conf,
            field_name="openbench.create.payload.test_confidence",
        )
        if not 0.0 < beta_conf < 1.0 or not 0.0 < alpha_conf < 1.0:
            raise ValueError("openbench.create.payload.test_confidence values must be within (0, 1)")

    test_max_games_raw = payload_cfg.test_max_games
    if isinstance(test_max_games_raw, str) and test_max_games_raw == "auto":
        max_games_val = sprt_cfg.max_games if sprt_cfg is not None and sprt_cfg.max_games is not None else 0
    else:
        max_games_val = _require_int(test_max_games_raw, field_name="openbench.create.payload.test_max_games")
    if test_mode == "GAMES" and max_games_val <= 0:
        raise ValueError("openbench.create.payload.test_max_games must be > 0 when test_mode=GAMES")

    scale_nps_raw = payload_cfg.scale_nps
    if isinstance(scale_nps_raw, str) and scale_nps_raw == "auto":
        scale_nps = _fetch_engine_nps(
            base_engine,
            openbench_server=openbench_server,
            openbench_username=openbench_username,
            openbench_password=openbench_password,
        )
    else:
        scale_nps = _require_int(scale_nps_raw, field_name="openbench.create.payload.scale_nps")
    if scale_nps <= 0:
        raise ValueError("openbench.create.payload.scale_nps must be > 0")

    dev_options = payload_cfg.dev_options.strip()
    base_options = payload_cfg.base_options.strip()
    for label, options in (("dev_options", dev_options), ("base_options", base_options)):
        if not re.search(r"\bThreads=\d+\b", options):
            raise ValueError(f"openbench.create.payload.{label} must include Threads=<int>")
        if not re.search(r"\bHash=\d+\b", options):
            raise ValueError(f"openbench.create.payload.{label} must include Hash=<int>")

    throughput = _require_positive_int(payload_cfg.throughput, field_name="openbench.create.payload.throughput")
    workload_size = _require_positive_int(
        payload_cfg.workload_size,
        field_name="openbench.create.payload.workload_size",
    )
    upload_pgns = payload_cfg.upload_pgns.strip().upper()
    if upload_pgns not in {"FALSE", "COMPACT", "VERBOSE"}:
        raise ValueError("openbench.create.payload.upload_pgns must be FALSE, COMPACT, or VERBOSE")
    scale_method = payload_cfg.scale_method.strip().upper()
    if scale_method not in {"DEV", "BASE", "BOTH"}:
        raise ValueError("openbench.create.payload.scale_method must be DEV, BASE, or BOTH")

    configured_book_name = payload_cfg.book_name.strip()
    book_name = _resolve_book_name(
        configured_book_name,
        openbench_server=openbench_server,
        openbench_username=openbench_username,
        openbench_password=openbench_password,
    )

    return {
        "dev_engine": dev_engine,
        "base_engine": base_engine,
        "dev_repo": dev_repo,
        "base_repo": base_repo,
        "dev_branch": dev_branch,
        "base_branch": base_branch,
        "dev_bench": payload_cfg.dev_bench.strip(),
        "base_bench": payload_cfg.base_bench.strip(),
        "dev_options": dev_options,
        "base_options": base_options,
        "dev_network": payload_cfg.dev_network.strip(),
        "base_network": payload_cfg.base_network.strip(),
        "dev_time_control": dev_tc,
        "base_time_control": base_tc,
        "book_name": book_name,
        "upload_pgns": upload_pgns,
        "test_mode": test_mode,
        "test_bounds": test_bounds,
        "test_confidence": test_conf,
        "test_max_games": str(max_games_val),
        "priority": str(payload_cfg.priority),
        "throughput": str(throughput),
        "workload_size": str(workload_size),
        "syzygy_wdl": payload_cfg.syzygy_wdl.strip().upper(),
        "syzygy_adj": payload_cfg.syzygy_adj.strip().upper(),
        "win_adj": payload_cfg.win_adj.strip(),
        "draw_adj": payload_cfg.draw_adj.strip(),
        "scale_method": scale_method,
        "scale_nps": str(scale_nps),
    }


def _resolve_repo_url(engine_spec: OpenBenchCreateEngineSpec, configured: str) -> str:
    candidate = configured.strip()
    if candidate and candidate != "auto":
        return candidate
    artifact = (engine_spec.artifact or "").strip()
    if not artifact:
        raise ValueError("openbench.create.payload.*_repo must be set when engine uses engine_path")
    repo_name = artifact.split("/", 1)[0]
    repo_obj = project_dirs.repos.get(repo_name)
    if not isinstance(repo_obj, RepoSettings):
        raise ValueError(
            f"openbench.create.payload repo URL for '{repo_name}' is missing. "
            "Set settings repos URL or specify *_repo explicitly."
        )
    repo = repo_obj
    if not repo.url:
        raise ValueError(
            f"openbench.create.payload repo URL for '{repo_name}' is missing. "
            "Set settings repos URL or specify *_repo explicitly."
        )
    return repo.url


def _resolve_branch(engine_spec: OpenBenchCreateEngineSpec, configured: str) -> str:
    candidate = configured.strip()
    if candidate and candidate != "auto":
        return candidate
    artifact = (engine_spec.artifact or "").strip()
    if not artifact:
        raise ValueError("openbench.create.payload.*_branch must be set when engine uses engine_path")
    commit = artifact.split("/", 1)[1]
    return commit


def _resolve_time_control(
    configured: str,
    *,
    base_tc: TimeControlLimits | None,
    engine_tc: TimeControlLimits | None,
) -> str:
    value = configured.strip()
    if value and value != "auto":
        return value
    limits = build_time_control_limits(base_tc, engine_tc)
    if limits is None:
        raise ValueError("openbench.create.payload.*_time_control is required (no rules.time_control configured)")
    if limits.node_limit is not None:
        return f"N={limits.node_limit}"
    if limits.depth_limit is not None:
        return f"D={limits.depth_limit}"
    if limits.fixed_time_ms is not None:
        return f"MT={limits.fixed_time_ms}"
    if limits.time_ms is None:
        raise ValueError("Unable to convert time control for OpenBench create payload")
    base_sec = limits.time_ms / 1000.0
    inc_ms = limits.increment_ms if limits.increment_ms is not None else limits.byoyomi_ms
    inc_sec = (inc_ms or 0) / 1000.0
    return f"{base_sec:.1f}+{inc_sec:.2f}"


def _fetch_engine_nps(
    engine_name: str,
    *,
    openbench_server: str,
    openbench_username: str,
    openbench_password: str,
) -> int:
    return fetch_engine_nps(
        engine_name,
        openbench_server=openbench_server,
        openbench_username=openbench_username,
        openbench_password=openbench_password,
    )


def _resolve_book_name(
    configured_book_name: str,
    *,
    openbench_server: str,
    openbench_username: str,
    openbench_password: str,
) -> str:
    if configured_book_name.upper() != "NONE":
        return configured_book_name
    try:
        books = _fetch_books(
            openbench_server=openbench_server,
            openbench_username=openbench_username,
            openbench_password=openbench_password,
        )
    except ValueError as exc:
        logger.warning("Failed to auto-resolve OpenBench book list: %s", exc)
        return configured_book_name
    if not books:
        return configured_book_name
    if any(book.upper() == "NONE" for book in books):
        return configured_book_name
    fallback = books[0]
    logger.warning(
        "openbench.create.payload.book_name=NONE is not available on server; falling back to '%s'",
        fallback,
    )
    return fallback


def _fetch_books(
    *,
    openbench_server: str,
    openbench_username: str,
    openbench_password: str,
) -> list[str]:
    return fetch_books(
        openbench_server=openbench_server,
        openbench_username=openbench_username,
        openbench_password=openbench_password,
    )


__all__ = [
    "OpenBenchCreateEngineSpec",
    "OpenBenchCreatePayloadInput",
    "OpenBenchCreatePayloadRequest",
    "OpenBenchCreateSprtSpec",
    "build_openbench_create_payload",
]
