"""Configuration resolution helpers for OpenBench delegate."""

from __future__ import annotations

import json
import os
from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.openbench.client_types import OpenBenchClientConfig
from shogiarena._core.contexts.game_session.adapters.openbench.create_payload_builder import (
    OpenBenchCreateEngineSpec,
    OpenBenchCreatePayloadInput,
    OpenBenchCreatePayloadRequest,
    OpenBenchCreateSprtSpec,
    build_openbench_create_payload,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.config_tournament import TournamentRunConfig
from shogiarena._core.platform.settings import facade as settings_mod
from shogiarena._core.shared.kernel.boundary_parsers.runner_state_payloads.parsers import (
    parse_tournament_run_state_boundary,
)
from shogiarena._core.shared.kernel.exceptions import ContractParseError
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_bool, coerce_int

from .scalar_validation import _require_float, _require_int, _require_positive_int


def _load_target_from_run_state(run_dir: Path, *, should_skip_resume: bool) -> int | None:
    run_state_path = run_dir / "run_state.json"
    if not run_state_path.exists() or should_skip_resume:
        return None
    try:
        with open(run_state_path, encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimeError("Failed to read run_state for OpenBench target reuse") from exc
    try:
        state = parse_tournament_run_state_boundary(raw, path=str(run_state_path))
    except ContractParseError as exc:
        raise RuntimeError("Failed to parse run_state for OpenBench target reuse") from exc
    openbench_state = state.get("openbench_state")
    if openbench_state is None:
        return None
    raw_target = openbench_state.get("target_test_id")
    target_id = coerce_int(raw_target)
    if target_id is not None and target_id > 0:
        return target_id
    return None


def _build_create_payload_request(config: TournamentRunConfig) -> OpenBenchCreatePayloadRequest:
    openbench_cfg = config.openbench
    create_cfg = openbench_cfg.create if openbench_cfg is not None else None
    if create_cfg is None or create_cfg.payload is None:
        raise ValueError("openbench.create.payload is required for mode=create_test")
    payload_cfg = create_cfg.payload

    payload = OpenBenchCreatePayloadInput(
        dev_engine=payload_cfg.dev_engine or "",
        base_engine=payload_cfg.base_engine or "",
        dev_repo=payload_cfg.dev_repo or "",
        base_repo=payload_cfg.base_repo or "",
        dev_branch=payload_cfg.dev_branch or "",
        base_branch=payload_cfg.base_branch or "",
        dev_options=payload_cfg.dev_options,
        base_options=payload_cfg.base_options,
        dev_time_control=payload_cfg.dev_time_control or "",
        base_time_control=payload_cfg.base_time_control or "",
        book_name=payload_cfg.book_name,
        scale_nps=payload_cfg.scale_nps,
        test_mode=payload_cfg.test_mode,
        test_bounds=payload_cfg.test_bounds,
        test_confidence=payload_cfg.test_confidence,
        test_max_games=payload_cfg.test_max_games,
        throughput=payload_cfg.throughput,
        workload_size=payload_cfg.workload_size,
        upload_pgns=payload_cfg.upload_pgns,
        scale_method=payload_cfg.scale_method,
        dev_bench=payload_cfg.dev_bench,
        base_bench=payload_cfg.base_bench,
        dev_network=payload_cfg.dev_network,
        base_network=payload_cfg.base_network,
        priority=payload_cfg.priority,
        syzygy_wdl=payload_cfg.syzygy_wdl,
        syzygy_adj=payload_cfg.syzygy_adj,
        win_adj=payload_cfg.win_adj,
        draw_adj=payload_cfg.draw_adj,
    )

    dev_source = config.engines[0]
    base_source = config.engines[1]
    sprt_source = config.sprt
    sprt = (
        OpenBenchCreateSprtSpec(
            elo0=_require_float(sprt_source.elo0, field_name="sprt.elo0"),
            elo1=_require_float(sprt_source.elo1, field_name="sprt.elo1"),
            alpha=_require_float(sprt_source.alpha, field_name="sprt.alpha"),
            beta=_require_float(sprt_source.beta, field_name="sprt.beta"),
            max_games=(
                _require_positive_int(sprt_source.max_games, field_name="sprt.max_games")
                if sprt_source.max_games is not None
                else None
            ),
        )
        if sprt_source is not None
        else None
    )

    return OpenBenchCreatePayloadRequest(
        payload=payload,
        dev_spec=OpenBenchCreateEngineSpec(
            artifact=dev_source.artifact,
            time_control=dev_source.time_control,
        ),
        base_spec=OpenBenchCreateEngineSpec(
            artifact=base_source.artifact,
            time_control=base_source.time_control,
        ),
        base_time_control=config.rules.time_control,
        sprt=sprt,
    )


def resolve_openbench_client_config(
    config: TournamentRunConfig,
    *,
    run_dir: Path,
    should_skip_resume: bool,
) -> OpenBenchClientConfig | None:
    raw = config.openbench
    if raw is None or not raw.is_enabled:
        return None
    if len(config.engines) != 2:
        raise ValueError("openbench.enabled=true requires exactly two engines")
    if config.sprt is None:
        raise ValueError("openbench.enabled=true requires sprt configuration")

    settings_cfg = settings_mod.SETTINGS.openbench
    server = raw.server or (settings_cfg.server if settings_cfg is not None else None)
    username = raw.username or (settings_cfg.username if settings_cfg is not None else None)
    raw_password_env = raw.password_env.strip()
    settings_password_env = (
        settings_cfg.password_env.strip() if settings_cfg is not None and settings_cfg.password_env else ""
    )
    if (
        raw_password_env == "OPENBENCH_PASSWORD"
        and settings_password_env
        and settings_password_env != "OPENBENCH_PASSWORD"
    ):
        password_env = settings_password_env
    else:
        password_env = raw_password_env or settings_password_env or "OPENBENCH_PASSWORD"
    password = os.environ.get(password_env, "").strip()
    if not password:
        raise ValueError(
            f"OpenBench password is missing. Set environment variable '{password_env}' (or override password_env)."
        )
    if not server:
        raise ValueError("OpenBench server is not configured")
    if not username:
        raise ValueError("OpenBench username is not configured")

    target_test_id: int | None
    raw_target_test_id = raw.target_test_id
    target_test_id = coerce_int(raw_target_test_id) if raw_target_test_id is not None else None
    mode = raw.mode
    if mode == "create_test" and target_test_id is None:
        restored = _load_target_from_run_state(run_dir, should_skip_resume=should_skip_resume)
        if restored is not None:
            target_test_id = restored

    create_payload: dict[str, str] | None = None
    create_discovery_timeout_sec = 180.0
    if mode == "create_test":
        create_payload = build_openbench_create_payload(
            _build_create_payload_request(config),
            openbench_server=server,
            openbench_username=username,
            openbench_password=password,
        )
        if raw.create is not None:
            create_discovery_timeout_sec = raw.create.discovery_timeout_sec

    submit_interval_games = _require_positive_int(
        raw.submit_interval_games,
        field_name="openbench.submit_interval_games",
    )
    heartbeat_interval_sec = _require_float(
        raw.heartbeat_interval_sec,
        field_name="openbench.heartbeat_interval_sec",
    )
    poll_interval_sec = _require_float(
        raw.poll_interval_sec,
        field_name="openbench.poll_interval_sec",
    )
    assignment_timeout_sec = _require_float(
        raw.assignment_timeout_sec,
        field_name="openbench.assignment_timeout_sec",
    )
    concurrency = max(
        1,
        _require_int(
            config.tournament.num_parallel,
            field_name="tournament.num_parallel",
        ),
    )
    create_discovery_timeout = _require_float(
        create_discovery_timeout_sec,
        field_name="openbench.create.discovery_timeout_sec",
    )

    return OpenBenchClientConfig(
        is_enabled=True,
        mode=mode,
        server=server,
        username=username,
        password=password,
        target_test_id=target_test_id,
        submit_interval_games=submit_interval_games,
        is_strict=coerce_bool(raw.is_strict),
        heartbeat_interval_sec=heartbeat_interval_sec,
        poll_interval_sec=poll_interval_sec,
        assignment_timeout_sec=assignment_timeout_sec,
        is_insecure_http_allowed=coerce_bool(raw.is_insecure_http_allowed),
        concurrency=concurrency,
        create_payload=create_payload,
        create_discovery_timeout_sec=create_discovery_timeout,
    )


__all__ = ["resolve_openbench_client_config"]
