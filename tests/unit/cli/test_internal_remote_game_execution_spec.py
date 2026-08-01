"""Remote internal CLIのGameExecutionSpec production wiring tests。"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    AdjudicationSpec,
    EngineExecutionSpec,
    EngineProcessSpec,
    EngineUsiSpec,
    ExecutionIdentity,
    GameExecutionResult,
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
from shogiarena._core.contexts.instances.adapters.engine_runtime_adapter import EngineRuntimeAdapter
from shogiarena._core.interfaces.cli.internal import remote
from shogiarena._core.interfaces.composition_root.default_root import build_default_root


def _sealed_spec(
    *,
    secret_ref: str | None = None,
    minimum_worker_version: str = "1.1.0",
) -> GameExecutionSpec:
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
                secret_environment_refs=(
                    {"ENGINE_TOKEN": secret_ref} if secret_ref is not None and engine_id == "black" else {}
                ),
            ),
            usi=EngineUsiSpec(),
        )

    time_control = GameTimeControlSpec(fixed_time_ms=100)
    payload = GameExecutionSpecPayload(
        minimum_worker_version=minimum_worker_version,
        identity=ExecutionIdentity(job_id="job", run_id="run", game_id="game"),
        black_engine=_engine("black", "a"),
        white_engine=_engine("white", "b"),
        opening=OpeningSpec(
            initial_sfen="startpos",
            black_engine_id="black",
            white_engine_id="white",
        ),
        time=GameTimeSpec(
            black=time_control,
            white=time_control,
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
        resources=ResourceRequirements(artifact_ids=["black-artifact", "white-artifact"]),
        output=OutputContract(),
    )
    return seal_game_execution_spec(payload)


class _WorkerStub:
    def __init__(self) -> None:
        self.received: GameExecutionSpec | None = None
        self.execution_root: Path | None = None
        self.secret_values: dict[str, str] = {}
        self.shutdown_requested = False

    async def execute(
        self,
        spec: GameExecutionSpec,
        *,
        execution_root: Path,
        progress_queue: asyncio.Queue[tuple[int, int, str | None]],
        secret_values: Mapping[str, str] | None = None,
    ) -> object:
        self.received = spec
        self.execution_root = execution_root
        self.secret_values = dict(secret_values or {})
        await progress_queue.put(
            (
                0,
                0,
                json.dumps(
                    {
                        "type": "move_progress",
                        "game_result": "draw",
                    }
                ),
            )
        )
        return SimpleNamespace(
            result=GameExecutionResult(
                execution_digest=spec.execution_digest,
                game_id=spec.identity.game_id,
                classification="DRAW_BY_REPETITION",
            )
        )

    def request_shutdown(self) -> None:
        self.shutdown_requested = True


def test_default_root_composes_game_execution_worker_with_watchdog_capable_adapter() -> None:
    root = build_default_root()

    assert isinstance(root.game_execution_worker, EngineRuntimeAdapter)


@pytest.mark.asyncio
async def test_remote_command_parses_sealed_spec_and_uses_composed_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _WorkerStub()
    monkeypatch.setattr(
        remote,
        "build_default_root",
        lambda: SimpleNamespace(game_execution_worker=worker),
    )
    spec_path = tmp_path / "spec.json"
    spec_path.write_bytes(_sealed_spec().canonical_json_bytes())

    await remote._remote_run_pair_command(argparse.Namespace(spec_file=str(spec_path)))  # noqa: SLF001

    assert worker.received == _sealed_spec()
    assert worker.execution_root == tmp_path
    assert worker.secret_values == {}


@pytest.mark.asyncio
async def test_remote_command_resolves_only_declared_secret_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    worker = _WorkerStub()
    monkeypatch.setattr(
        remote,
        "build_default_root",
        lambda: SimpleNamespace(game_execution_worker=worker),
    )
    monkeypatch.setenv("SHOGIARENA_SECRET_ENGINE_TOKEN", "secret-value")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-be-inherited")
    spec = _sealed_spec(secret_ref="SHOGIARENA_SECRET_ENGINE_TOKEN")
    spec_path = tmp_path / "spec.json"
    spec_path.write_bytes(spec.canonical_json_bytes())

    await remote._remote_run_pair_command(argparse.Namespace(spec_file=str(spec_path)))  # noqa: SLF001

    assert worker.received == spec
    assert worker.secret_values == {"SHOGIARENA_SECRET_ENGINE_TOKEN": "secret-value"}
    assert "secret-value" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_remote_command_rejects_unknown_version_before_worker_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _WorkerStub()
    monkeypatch.setattr(
        remote,
        "build_default_root",
        lambda: SimpleNamespace(game_execution_worker=worker),
    )
    payload = _sealed_spec().model_dump(mode="json")
    payload["schema_version"] = "shogiarena.game-execution-spec.v999"
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        await remote._remote_run_pair_command(argparse.Namespace(spec_file=str(spec_path)))  # noqa: SLF001

    assert exc_info.value.code == 2
    assert worker.received is None


@pytest.mark.asyncio
async def test_remote_command_rejects_worker_below_minimum_version_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    worker = _WorkerStub()
    monkeypatch.setattr(remote, "__version__", "1.1.0")
    monkeypatch.setattr(
        remote,
        "build_default_root",
        lambda: SimpleNamespace(game_execution_worker=worker),
    )
    spec_path = tmp_path / "spec.json"
    spec_path.write_bytes(_sealed_spec(minimum_worker_version="1.2.0").canonical_json_bytes())

    with pytest.raises(SystemExit) as exc_info:
        await remote._remote_run_pair_command(argparse.Namespace(spec_file=str(spec_path)))  # noqa: SLF001

    assert exc_info.value.code == 2
    assert worker.received is None
    assert "worker version 1.1.0 does not satisfy minimum 1.2.0" in capsys.readouterr().out
