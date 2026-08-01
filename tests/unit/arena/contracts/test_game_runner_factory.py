"""GameExecutionSpec共通runner factory tests (task 0054 Phase 2.3)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_materializer import (
    materialize_engine_config,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_output import (
    build_game_execution_result,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_worker import (
    GameExecutionWorkerAdapter,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_runner_factory import (
    build_game_runner_execution_policy,
)
from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    AdjudicationSpec,
    EngineExecutionSpec,
    EngineProcessSpec,
    EngineUsiSpec,
    ExecutionIdentity,
    GameExecutionSpec,
    GameExecutionSpecPayload,
    GameRulesSpec,
    GameTimeControlSpec,
    GameTimeSpec,
    OpeningSpec,
    OutputContract,
    PlatformArtifactRef,
    RepetitionSpec,
    ResourceRequirements,
    TargetPlatform,
    TimeoutPolicySpec,
    seal_game_execution_spec,
)
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.shared.kernel.runtime_watchdog import RuntimeWatchdog


def _payload() -> GameExecutionSpecPayload:
    platform = TargetPlatform(operating_system="linux", architecture="x86_64")

    def _engine(engine_id: str, digest_char: str) -> EngineExecutionSpec:
        return EngineExecutionSpec(
            engine_id=engine_id,
            process=EngineProcessSpec(
                artifact=PlatformArtifactRef(
                    logical_id=f"{engine_id}-artifact",
                    kind="engine_binary",
                    sha256=digest_char * 64,
                    target_platform=platform,
                    entrypoint="engine",
                ),
                working_directory=f"engines/{engine_id}",
                handshake_timeout_ms=10_000,
            ),
            usi=EngineUsiSpec(),
        )

    return GameExecutionSpecPayload(
        minimum_worker_version="1.1.0",
        identity=ExecutionIdentity(job_id="job", run_id="run", game_id="game"),
        black_engine=_engine("black", "a"),
        white_engine=_engine("white", "b"),
        opening=OpeningSpec(
            initial_sfen="startpos",
            black_engine_id="black",
            white_engine_id="white",
        ),
        time=GameTimeSpec(
            black=GameTimeControlSpec(fixed_time_ms=100),
            white=GameTimeControlSpec(fixed_time_ms=100),
            startup_grace_ms=1_000,
            outer_deadline_ms=10_000,
        ),
        rules=GameRulesSpec(
            adjudication=AdjudicationSpec(),
            repetition=RepetitionSpec(),
        ),
        timeout=TimeoutPolicySpec(
            watchdog="disabled",
            origin_attribution="best_effort",
            reclassification="disabled",
        ),
        resources=ResourceRequirements(
            artifact_ids=["black-artifact", "white-artifact"],
        ),
        output=OutputContract(),
    )


class _RuntimeEngineStub:
    @property
    def engine_info(self) -> dict[str, str]:
        return {}

    async def apply_engine_options(
        self,
        options: object,
        *,
        clear_hash: bool = True,
        after_setoption: str = "isready",
        validation: object | None = None,
    ) -> None:
        return None

    async def close(self) -> None:
        return None


def test_factory_maps_time_adjudication_repetition_and_timeout_exactly() -> None:
    payload = _payload()
    payload = payload.model_copy(
        update={
            "time": GameTimeSpec(
                black=GameTimeControlSpec(time_ms=10_000, increment_ms=100),
                white=GameTimeControlSpec(fixed_time_ms=250),
                startup_grace_ms=30_000,
                outer_deadline_ms=120_000,
            ),
            "rules": GameRulesSpec(
                adjudication=AdjudicationSpec(
                    resign_threshold_cp=1234,
                    resign_move_count=6,
                    resign_two_sided=False,
                    max_plies=222,
                    sync_max_plies_with_engine=True,
                ),
                repetition=RepetitionSpec(occurrences_to_draw=4),
            ),
            "timeout": TimeoutPolicySpec(
                watchdog="required",
                origin_attribution="required",
                reclassification="invalid_on_coordinator_stall",
            ),
        }
    )
    spec = seal_game_execution_spec(payload)

    policy = build_game_runner_execution_policy(spec, runtime_watchdog=RuntimeWatchdog())

    assert policy.black_limits.time_ms == 10_000
    assert policy.black_limits.increment_ms == 100
    assert policy.white_limits.fixed_time_ms == 250
    assert policy.runner.repetition_occurrences_to_draw == 4
    assert policy.runner._timeout_reclassification_enabled is True  # noqa: SLF001
    adjudication = policy.runner.adjudication_config
    assert adjudication is not None
    assert adjudication.is_resign_enabled is True
    assert adjudication.resign_score_cp == 1234
    assert adjudication.resign_move_count == 6
    assert adjudication.is_resign_two_sided is False
    assert adjudication.is_max_plies_enabled is True
    assert adjudication.max_plies == 222


def test_factory_disables_optional_policies_without_implicit_defaults() -> None:
    payload = _payload()
    payload = payload.model_copy(
        update={
            "rules": GameRulesSpec(
                adjudication=AdjudicationSpec(
                    resign_threshold_cp=None,
                    max_plies=None,
                ),
                repetition=RepetitionSpec(occurrences_to_draw=2),
            ),
            "timeout": TimeoutPolicySpec(
                watchdog="disabled",
                origin_attribution="best_effort",
                reclassification="disabled",
            ),
        }
    )

    policy = build_game_runner_execution_policy(seal_game_execution_spec(payload))

    adjudication = policy.runner.adjudication_config
    assert adjudication is not None
    assert adjudication.is_resign_enabled is False
    assert adjudication.is_max_plies_enabled is False
    assert policy.runner._timeout_reclassification_enabled is False  # noqa: SLF001


def test_factory_rejects_required_watchdog_without_runtime_probe() -> None:
    payload = _payload().model_copy(
        update={
            "timeout": TimeoutPolicySpec(
                watchdog="required",
                origin_attribution="required",
                reclassification="invalid_on_coordinator_stall",
            )
        }
    )
    with pytest.raises(ValueError, match="requires a runtime watchdog"):
        build_game_runner_execution_policy(seal_game_execution_spec(payload))


def test_factory_injects_required_runtime_watchdog() -> None:
    payload = _payload().model_copy(
        update={
            "timeout": TimeoutPolicySpec(
                watchdog="required",
                origin_attribution="required",
                reclassification="invalid_on_coordinator_stall",
            )
        }
    )
    watchdog = RuntimeWatchdog()

    policy = build_game_runner_execution_policy(
        seal_game_execution_spec(payload),
        runtime_watchdog=watchdog,
    )

    assert policy.runner._runtime_watchdog is watchdog  # noqa: SLF001


def test_output_contract_enforces_required_provenance() -> None:
    payload = _payload().model_copy(
        update={
            "output": OutputContract(
                required_provenance=[
                    "engine_artifacts",
                    "engine_identity",
                    "effective_options",
                    "timestamps",
                ]
            )
        }
    )
    now = datetime.now(UTC)

    result = build_game_execution_result(
        seal_game_execution_spec(payload),
        classification="DRAW_BY_REPETITION",
        started_at=now,
        finished_at=now,
        engine_info={"black": {"name": "black"}, "white": {"name": "white"}},
    )

    assert set(result.provenance) == {
        "engine_artifacts",
        "engine_identity",
        "effective_options",
        "timestamps",
    }


def test_output_contract_rejects_unavailable_required_provenance() -> None:
    payload = _payload().model_copy(update={"output": OutputContract(required_provenance=["endpoint"])})

    with pytest.raises(ValueError, match="required output provenance is unavailable"):
        build_game_execution_result(
            seal_game_execution_spec(payload),
            classification="DRAW_BY_REPETITION",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )


def test_output_contract_rejects_unsupported_record_policy() -> None:
    payload = _payload().model_copy(update={"output": OutputContract(record_policy="disabled")})

    with pytest.raises(ValueError, match="record_policy='required'"):
        build_game_execution_result(
            seal_game_execution_spec(payload),
            classification="DRAW_BY_REPETITION",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )


def test_runner_factory_does_not_mutate_canonical_digest() -> None:
    spec = seal_game_execution_spec(_payload())
    digest = spec.execution_digest

    watchdog = RuntimeWatchdog()
    policy = build_game_runner_execution_policy(spec, runtime_watchdog=watchdog)

    assert policy.black_limits.time_ms == spec.time.black.time_ms
    assert spec.execution_digest == digest


def test_local_locator_and_remote_wire_boundary_preserve_same_canonical_spec_digest(tmp_path: Path) -> None:
    local_spec = seal_game_execution_spec(_payload())
    remote_spec = GameExecutionSpec.model_validate(json.loads(local_spec.canonical_json_bytes()))
    local_engine_path = tmp_path / "engine-a"

    local_mapping = materialize_engine_config(
        local_spec.black_engine,
        execution_root=tmp_path,
        artifact_paths={local_spec.black_engine.process.artifact.logical_id: local_engine_path},
    )
    remote_mapping = materialize_engine_config(
        remote_spec.black_engine,
        execution_root=Path("/remote-worker"),
    )

    assert local_mapping["engine_args"] == remote_mapping["engine_args"]
    assert local_mapping["options"] == remote_mapping["options"]
    assert local_spec.execution_digest == remote_spec.execution_digest


@pytest.mark.asyncio
async def test_one_shot_worker_rejects_reuse_lifecycle_before_engine_creation() -> None:
    worker = GameExecutionWorkerAdapter(
        engine_factory_service=build_default_root().engine_factory_service,
        instance_pool=InstancePool.ensure_default_local_pool(),
    )

    with pytest.raises(ValueError, match="requires engine lifecycle 'per_game'"):
        await worker.execute(
            seal_game_execution_spec(_payload()),
            execution_root=Path.cwd(),
            progress_queue=asyncio.Queue(),
        )


@pytest.mark.asyncio
async def test_one_shot_worker_passes_composed_local_pool_to_engine_factory(tmp_path: Path) -> None:
    instance_pool = InstancePool.ensure_default_local_pool()
    create_engine = AsyncMock(return_value=_RuntimeEngineStub())
    factory_service = cast(
        EngineFactoryService,
        SimpleNamespace(create_engine_from_mapping=create_engine),
    )
    worker = GameExecutionWorkerAdapter(
        engine_factory_service=factory_service,
        instance_pool=instance_pool,
    )
    engine_spec = seal_game_execution_spec(_payload()).black_engine

    await worker._create_engine(  # noqa: SLF001
        engine_spec,
        execution_root=tmp_path,
        secret_values={},
    )

    assert create_engine.await_args.kwargs["instance_pool"] is instance_pool
    assert create_engine.await_args.args[0]["engine_path"] == str(tmp_path / "engines" / "black" / "engine")
