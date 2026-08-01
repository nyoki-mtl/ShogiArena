"""GameExecutionSpec worker adapter。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from rsshogi.types import Color

from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_materializer import (
    apply_engine_variant,
    materialize_engine_config,
    materialize_opening_sfen,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution_output import (
    build_game_execution_result,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.game_runner_factory import (
    build_game_runner_execution_policy,
)
from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    EngineExecutionSpec,
    GameExecutionSpec,
)
from shogiarena._core.contexts.game_session.ports.game_execution_worker import GameExecutionOutcome
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.match.application.engine_participant import EngineParticipant
from shogiarena._core.contexts.match.application.runner import GameRunner
from shogiarena._core.shared.kernel.game_results import game_result_name
from shogiarena._core.shared.kernel.runtime_watchdog import RuntimeWatchdog


@runtime_checkable
class _RuntimeEnginePort(Protocol):
    @property
    def engine_info(self) -> Mapping[str, str]: ...

    async def apply_engine_options(
        self,
        options: Mapping[str, object] | None,
        *,
        clear_hash: bool = True,
        after_setoption: str = "isready",
        validation: object | None = None,
    ) -> None: ...

    async def close(self) -> None: ...


class GameExecutionWorkerAdapter:
    """共通runner factoryとengine factoryを使うworker実装。"""

    def __init__(
        self,
        *,
        engine_factory_service: EngineFactoryService,
        instance_pool: InstancePool,
    ) -> None:
        self._engine_factory_service = engine_factory_service
        self._instance_pool = instance_pool
        self._active_runner: GameRunner | None = None

    def request_shutdown(self) -> None:
        """実行中runnerへ停止を伝播する。"""

        if self._active_runner is not None:
            self._active_runner.request_shutdown()

    async def execute(
        self,
        spec: GameExecutionSpec,
        *,
        execution_root: Path,
        progress_queue: asyncio.Queue[tuple[int, int, str | None]],
        secret_values: Mapping[str, str] | None = None,
    ) -> GameExecutionOutcome:
        """封印済みspecを変更せずに一局実行する。"""

        lifecycles = {
            spec.black_engine.process.lifecycle,
            spec.white_engine.process.lifecycle,
        }
        if lifecycles != {"per_game"}:
            raise ValueError("one-shot GameExecutionWorker requires engine lifecycle 'per_game'")
        watchdog = RuntimeWatchdog() if spec.timeout.watchdog == "required" else None
        if watchdog is not None:
            watchdog.start()
        policy = build_game_runner_execution_policy(
            spec,
            progress_queue=progress_queue,
            runtime_watchdog=watchdog,
        )
        self._active_runner = policy.runner
        black_engine: _RuntimeEnginePort | None = None
        white_engine: _RuntimeEnginePort | None = None
        black_participant: EngineParticipant | None = None
        white_participant: EngineParticipant | None = None
        try:
            started_at = datetime.now(UTC)
            black_engine = await self._create_engine(
                spec.black_engine,
                execution_root=execution_root,
                secret_values=secret_values,
            )
            white_engine = await self._create_engine(
                spec.white_engine,
                execution_root=execution_root,
                secret_values=secret_values,
            )
            await apply_engine_variant(black_engine, spec.black_engine)
            await apply_engine_variant(white_engine, spec.white_engine)
            black_participant = EngineParticipant(
                black_engine,
                name_override=spec.black_engine.engine_id,
                role=Color.BLACK,
            )
            white_participant = EngineParticipant(
                white_engine,
                name_override=spec.white_engine.engine_id,
                role=Color.WHITE,
            )
            record = await policy.runner.run_game(
                black_participant,
                white_participant,
                initial_sfen=materialize_opening_sfen(spec.opening),
                game_id=spec.identity.game_id,
                black_time_control_limits=policy.black_limits,
                white_time_control_limits=policy.white_limits,
            )
            return GameExecutionOutcome(
                record=record,
                result=build_game_execution_result(
                    spec,
                    classification=game_result_name(record.result),
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    engine_info={
                        "black": black_engine.engine_info,
                        "white": white_engine.engine_info,
                    },
                ),
            )
        finally:
            self._active_runner = None
            await _shutdown_engines(
                black_participant=black_participant,
                white_participant=white_participant,
                black_engine=black_engine,
                white_engine=white_engine,
            )
            if watchdog is not None:
                await watchdog.stop()

    async def _create_engine(
        self,
        spec: EngineExecutionSpec,
        *,
        execution_root: Path,
        secret_values: Mapping[str, str] | None,
    ) -> _RuntimeEnginePort:
        process = spec.process
        entrypoint = process.artifact.entrypoint
        if entrypoint is None:
            raise ValueError("engine artifact entrypoint is required")
        config_mapping = materialize_engine_config(
            spec,
            execution_root=execution_root,
            secret_values=secret_values,
        )
        engine = await self._engine_factory_service.create_engine_from_mapping(
            config_mapping,
            timeout=process.handshake_timeout_ms / 1000.0,
            engine_name=spec.engine_id,
            instance_pool=self._instance_pool,
            collect_info_strings=process.collect_info_strings,
            collect_raw_io=process.collect_raw_io,
            collect_stderr=process.collect_stderr,
            collect_outbound=process.collect_outbound,
            option_validation=spec.usi.option_validation,
        )
        if not isinstance(engine, _RuntimeEnginePort):
            raise TypeError("engine factory result does not support runtime option application")
        return engine


async def _shutdown_engines(
    *,
    black_participant: EngineParticipant | None,
    white_participant: EngineParticipant | None,
    black_engine: _RuntimeEnginePort | None,
    white_engine: _RuntimeEnginePort | None,
) -> None:
    tasks = []
    tasks.append(black_participant.shutdown() if black_participant is not None else _close(black_engine))
    tasks.append(white_participant.shutdown() if white_participant is not None else _close(white_engine))
    await asyncio.gather(*tasks, return_exceptions=True)


async def _close(engine: _RuntimeEnginePort | None) -> None:
    if engine is not None:
        await engine.close()


__all__ = ["GameExecutionWorkerAdapter"]
