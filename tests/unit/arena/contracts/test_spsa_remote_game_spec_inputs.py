"""SPSA remote callerのstable spec input regression。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
from shogiarena._core.contexts.spsa.adapters import orchestrator_remote_game_mixin
from shogiarena._core.contexts.spsa.adapters.orchestrator_remote_game_mixin import (
    SpsaOrchestratorRemoteGameMixin,
)
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


@pytest.mark.asyncio
async def test_spsa_remote_spec_uses_stable_engine_ids_and_common_default_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def prepare(*_args: object, **kwargs: object) -> tuple[object, str, dict[str, object]]:
        captured.update(kwargs)
        return object(), "~/arena", {}

    class _GameInfo:
        result = SimpleNamespace(value="draw")

        def update_metadata(self, _payload: object) -> None:
            return None

    async def execute(*_args: object, **_kwargs: object) -> object:
        now = datetime.now(UTC)
        return SimpleNamespace(
            game_info=_GameInfo(),
            started_at=now,
            completed_at=now,
            endpoint_identity="endpoint",
            deployment_id="d" * 64,
            job_id="job-" + "1" * 32,
            attempt_id="attempt-" + "2" * 32,
            worker_result=SimpleNamespace(execution_digest="a" * 64, provenance={}),
        )

    @asynccontextmanager
    async def lifecycle(*_args: object, **_kwargs: object) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(orchestrator_remote_game_mixin, "_prepare_remote_game_spec_service", prepare)
    monkeypatch.setattr(orchestrator_remote_game_mixin, "_execute_remote_pair_game_service", execute)
    monkeypatch.setattr(orchestrator_remote_game_mixin, "manage_remote_pair_instance_lifecycle", lifecycle)
    monkeypatch.setattr(
        orchestrator_remote_game_mixin,
        "collect_participation_records_remote_pair",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        orchestrator_remote_game_mixin,
        "attach_spsa_participation_identity",
        lambda records, **_kwargs: records,
    )

    tuned = EngineConfig(name="stable-tuned", go_options={"nodes": 123})
    baseline = EngineConfig(name="stable-baseline", go_options={"depth": 7})
    owner = SpsaOrchestratorRemoteGameMixin()
    owner.config = SimpleNamespace(
        tuned=[tuned],
        baseline=[baseline],
        rules=SimpleNamespace(adjudication=SimpleNamespace(is_max_plies_enabled=True, max_plies=240)),
    )
    owner.tuned_config = Path("tuned.yaml")
    owner.baseline_config = Path("baseline.yaml")
    owner.extra_options = None
    owner._engine_factory_service = object()
    owner._default_engine_handshake_timeout = None
    owner._timeout_reclassification_enabled = True
    owner.session_context = SimpleNamespace(run_id="run")
    limits = TimeControlLimits(fixed_time_ms=100)

    await owner._run_remote_game(
        start_sfen="startpos",
        tuned_params=[],
        current_params=[],
        is_tuned_as_black=True,
        game_id="game",
        black_limits=limits,
        white_limits=limits,
        remote_instance=SimpleNamespace(name="ssh-a"),
        tuned_label="v000123-plus",
        baseline_label="v000123-baseline",
        tuned_options={"ParamA": 10},
        baseline_options={"ParamA": 0},
        black_engine_id="stable-tuned",
        white_engine_id="stable-baseline",
        tuned_variant_id="plus",
        baseline_variant_id="baseline",
        clear_hash_before_game=False,
        after_variant_setoption="none",
        black_item=SimpleNamespace(pool_key="stable-tuned"),
        white_item=SimpleNamespace(pool_key="stable-baseline"),
        update_idx=123,
        pair_id="update-123",
        observation_kind="SPSA",
    )

    assert captured["black_name"] == "stable-tuned"
    assert captured["white_name"] == "stable-baseline"
    assert captured["black_handshake_timeout_s"] == 120.0
    assert captured["white_handshake_timeout_s"] == 120.0
    assert captured["black_variant_id"] == "plus"
    assert captured["clear_hash_before_game"] is False
