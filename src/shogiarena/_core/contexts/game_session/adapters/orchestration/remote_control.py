"""Remote execution control extracted from BaseOrchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from rsshogi.core import normalize_usi_position

from shogiarena._core.contexts.game_session.adapters.orchestration.config_builders import (
    UsiOptionLayers,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_manifest import (
    persist_game_execution_manifest,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_spec_resolver import (
    EngineSpecResolveRequest,
    resolve_engine_execution,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.remote_executor import RemoteExecutor
from shogiarena._core.contexts.game_session.application.preplaced_resource_mapping import (
    remote_engine_artifact_logical_id,
)
from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    AdjudicationSpec,
    ExecutionIdentity,
    GameExecutionSpecPayload,
    GameRulesSpec,
    GameTimeControlSpec,
    GameTimeSpec,
    OpeningSpec,
    OutputContract,
    RepetitionSpec,
    ResourceRequirements,
    TargetPlatform,
    TimeoutPolicySpec,
    seal_game_execution_spec,
)
from shogiarena._core.contexts.instances.application.instance_models import Instance
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.instances.ports.orchestrator_primitives import remote_project_root
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonScalar
from shogiarena._core.shared.kernel.service_ports import ArtifactResolutionPort
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


def get_remote_executor(
    orchestrator: Any,
    remote_instance: Instance,
    *,
    artifact_resolver: ArtifactResolutionPort | None = None,
) -> RemoteExecutor:
    """Return a cached RemoteExecutor for the provided instance."""

    owner = orchestrator
    name = remote_instance.name
    if not name:
        raise ValueError("remote_instance must provide a non-empty name")
    existing = owner._remote_executors.get(name)
    if existing is not None:
        return existing
    worker_bundle = getattr(owner, "_remote_worker_bundle", None)
    if worker_bundle is None:
        raise RuntimeError("Remote execution requires a worker bundle sealed into the run provenance")
    executor = RemoteExecutor(
        remote_instance,
        worker_bundle,
        artifact_resolver=artifact_resolver,
    )
    owner._remote_executors[name] = executor
    return executor


async def ensure_remote_deployment(
    orchestrator: Any,
    executor: RemoteExecutor,
    remote_instance: Instance,
    remote_root: str | None = None,
) -> str:
    """Ensure the remote deployment is immutable and return the instance root."""

    del orchestrator
    name = remote_instance.name
    if not name:
        raise ValueError("remote_instance must provide a non-empty name")
    resolved_root = remote_root or remote_project_root(remote_instance)
    await executor.ensure_deployment(resolved_root)
    return resolved_root


async def _resolve_remote_binaries(executor: RemoteExecutor, *config_paths: Path) -> list[Path]:
    """Remote platform binariesをlocal artifactとして解決する。"""

    resolved: list[Path] = []
    for config_path in config_paths:
        local_binary = await executor.resolve_local_binary(config_path)
        resolved.append(local_binary.resolve())
    return resolved


async def prepare_remote_game_spec(
    orchestrator: Any,
    *,
    remote_instance: Instance,
    black_config_path: Path,
    white_config_path: Path,
    black_option_layers: UsiOptionLayers,
    white_option_layers: UsiOptionLayers,
    black_variant_options: JsonObject | None = None,
    white_variant_options: JsonObject | None = None,
    black_path_option_names: tuple[str, ...] = (),
    white_path_option_names: tuple[str, ...] = (),
    black_go_options: JsonObject,
    white_go_options: JsonObject,
    black_handshake_timeout_s: float,
    white_handshake_timeout_s: float,
    black_variant_id: str | None = None,
    white_variant_id: str | None = None,
    clear_hash_before_game: bool = False,
    after_variant_setoption: str = "none",
    start_sfen: str,
    game_id: str,
    run_id: str,
    black_name: str,
    white_name: str,
    black_limits: TimeControlLimits,
    white_limits: TimeControlLimits,
    max_plies: int,
    rules: Any,
    timeout_reclassification_enabled: bool,
    engine_factory_service: EngineFactoryService,
    update_idx: int | None = None,
    pair_id: str | None = None,
) -> tuple[RemoteExecutor, str, JsonObject]:
    """Prepare executor, remote root, and run spec for remote pair execution."""

    executor = get_remote_executor(
        orchestrator,
        remote_instance,
        artifact_resolver=engine_factory_service.artifact_resolver,
    )
    remote_root = await ensure_remote_deployment(orchestrator, executor, remote_instance)
    black_local_bin, white_local_bin = await _resolve_remote_binaries(
        executor,
        black_config_path,
        white_config_path,
    )
    black_artifact_overlay = _scalar_options(
        black_option_layers.artifact_overlay,
        field_name="black_artifact_overlay",
    )
    white_artifact_overlay = _scalar_options(
        white_option_layers.artifact_overlay,
        field_name="white_artifact_overlay",
    )
    black_arena = _scalar_options(black_option_layers.arena, field_name="black_arena_options")
    white_arena = _scalar_options(white_option_layers.arena, field_name="white_arena_options")
    black_overlays = _scalar_options(
        black_option_layers.declared_overlays,
        field_name="black_overlay_options",
    )
    white_overlays = _scalar_options(
        white_option_layers.declared_overlays,
        field_name="white_overlay_options",
    )
    black_inline = _scalar_options(black_option_layers.inline, field_name="black_inline_options")
    white_inline = _scalar_options(white_option_layers.inline, field_name="white_inline_options")
    black_variant = _scalar_options(black_variant_options, field_name="black_variant_options")
    white_variant = _scalar_options(white_variant_options, field_name="white_variant_options")
    black_resolved = resolve_engine_execution(
        EngineSpecResolveRequest(
            config_path=black_config_path,
            engine_id=black_name,
            artifact_logical_id=remote_engine_artifact_logical_id(black_name),
            target_platform=TargetPlatform(operating_system="linux", architecture="x86_64"),
            engine_source_override=black_local_bin,
            artifact_overlay_options=black_artifact_overlay,
            arena_options=black_arena,
            overlay_options=black_overlays,
            inline_options=black_inline,
            variant_options=black_variant,
            go_options=_scalar_options(black_go_options, field_name="black_go_options"),
            path_option_names=black_path_option_names,
            variant_id=black_variant_id,
            lifecycle="per_game",
            clear_hash_before_game=clear_hash_before_game,
            after_variant_setoption=_after_setoption(after_variant_setoption),
            default_handshake_timeout_s=black_handshake_timeout_s,
        )
    )
    white_resolved = resolve_engine_execution(
        EngineSpecResolveRequest(
            config_path=white_config_path,
            engine_id=white_name,
            artifact_logical_id=remote_engine_artifact_logical_id(white_name),
            target_platform=TargetPlatform(operating_system="linux", architecture="x86_64"),
            engine_source_override=white_local_bin,
            artifact_overlay_options=white_artifact_overlay,
            arena_options=white_arena,
            overlay_options=white_overlays,
            inline_options=white_inline,
            variant_options=white_variant,
            go_options=_scalar_options(white_go_options, field_name="white_go_options"),
            path_option_names=white_path_option_names,
            variant_id=white_variant_id,
            lifecycle="per_game",
            clear_hash_before_game=clear_hash_before_game,
            after_variant_setoption=_after_setoption(after_variant_setoption),
            default_handshake_timeout_s=white_handshake_timeout_s,
        )
    )
    black_spec = black_resolved.spec
    white_spec = white_resolved.spec
    await executor.provision_game_engine_artifacts(
        remote_root=remote_root,
        spec=black_spec,
        engine_source=black_local_bin,
        path_sources=black_resolved.provenance.path_sources,
    )
    await executor.provision_game_engine_artifacts(
        remote_root=remote_root,
        spec=white_spec,
        engine_source=white_local_bin,
        path_sources=white_resolved.provenance.path_sources,
    )
    adjudication = rules.adjudication
    artifact_ids = sorted(
        {
            black_spec.process.artifact.logical_id,
            white_spec.process.artifact.logical_id,
            *(resource.artifact.logical_id for resource in black_spec.usi.path_resources),
            *(resource.artifact.logical_id for resource in white_spec.usi.path_resources),
        }
    )
    spec = seal_game_execution_spec(
        GameExecutionSpecPayload(
            minimum_worker_version=executor.worker_bundle.manifest.package_version,
            identity=ExecutionIdentity(
                job_id=f"{run_id}:{game_id}",
                run_id=run_id,
                game_id=game_id,
                update_idx=update_idx,
                pair_id=pair_id,
            ),
            black_engine=black_spec,
            white_engine=white_spec,
            opening=OpeningSpec(
                initial_sfen=normalize_usi_position(start_sfen),
                black_engine_id=black_name,
                white_engine_id=white_name,
            ),
            time=GameTimeSpec(
                black=_time_control_spec(black_limits),
                white=_time_control_spec(white_limits),
                startup_grace_ms=30_000,
                outer_deadline_ms=_outer_deadline_ms(
                    black_limits,
                    white_limits,
                    max_plies=max_plies,
                ),
            ),
            rules=GameRulesSpec(
                adjudication=AdjudicationSpec(
                    resign_threshold_cp=adjudication.resign_threshold_cp,
                    resign_move_count=adjudication.resign_move_count,
                    resign_two_sided=adjudication.is_resign_two_sided,
                    max_plies=max_plies or None,
                    sync_max_plies_with_engine=adjudication.should_sync_max_plies_with_engine,
                ),
                repetition=RepetitionSpec(
                    occurrences_to_draw=rules.repetition_occurrences_to_draw,
                ),
            ),
            timeout=TimeoutPolicySpec(
                watchdog="required",
                origin_attribution="required",
                reclassification=("invalid_on_coordinator_stall" if timeout_reclassification_enabled else "disabled"),
            ),
            resources=ResourceRequirements(
                required_tags=[],
                artifact_ids=artifact_ids,
            ),
            output=OutputContract(
                required_provenance=[
                    "engine_artifacts",
                    "engine_identity",
                    "effective_options",
                    "timestamps",
                ],
            ),
        )
    )
    persist_game_execution_manifest(
        run_dir=Path(orchestrator.run_dir),
        game_id=game_id,
        payload={
            "game_execution_spec": spec.model_dump(mode="json"),
            "engine_provenance": [
                black_resolved.provenance.to_payload(),
                white_resolved.provenance.to_payload(),
            ],
        },
    )
    return executor, remote_root, spec.model_dump(mode="json")


def _time_control_spec(limits: TimeControlLimits) -> GameTimeControlSpec:
    return GameTimeControlSpec.model_validate(limits.model_dump(mode="python"))


def _outer_deadline_ms(
    black_limits: TimeControlLimits,
    white_limits: TimeControlLimits,
    *,
    max_plies: int,
) -> int:
    plies = max_plies if max_plies > 0 else 320
    per_move_cap = max(black_limits.max_wait_ms, white_limits.max_wait_ms)
    return 30_000 + plies * per_move_cap + 10_000


def _scalar_options(
    options: JsonObject | None,
    *,
    field_name: str,
) -> dict[str, JsonScalar]:
    result: dict[str, JsonScalar] = {}
    for name, value in (options or {}).items():
        if value is None or isinstance(value, str | int | float | bool):
            result[str(name)] = value
            continue
        raise TypeError(f"{field_name}.{name} must be a scalar JSON value")
    return result


def _after_setoption(value: str) -> Literal["none", "isready"]:
    if value == "none":
        return "none"
    if value == "isready":
        return "isready"
    raise ValueError("after_variant_setoption must be one of: none, isready")


__all__ = [
    "ensure_remote_deployment",
    "get_remote_executor",
    "prepare_remote_game_spec",
]
