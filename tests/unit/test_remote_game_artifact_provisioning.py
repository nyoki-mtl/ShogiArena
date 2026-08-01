"""Transport-neutral GameExecutionSpec artifact provisioning tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.remote_executor import RemoteExecutor
from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    EngineExecutionSpec,
    EngineProcessSpec,
    EngineUsiSpec,
    PathResourceRef,
    PlatformArtifactRef,
    TargetPlatform,
)
from shogiarena._core.platform.engine_provisioning.remote_deployment import PreparedRemoteDeployment
from shogiarena._core.platform.engine_provisioning.remote_paths import RemotePosixPath


@pytest.mark.asyncio
async def test_remote_deployment_is_reused_for_the_same_endpoint_and_root() -> None:
    class _Transport:
        async def connect(self) -> None:
            return

        async def endpoint_identity(self) -> str:
            return "ssh://worker@example:22#host-key"

    class _Resolver:
        async def expand(self, path: RemotePosixPath) -> RemotePosixPath:
            return path

    class _Manager:
        def __init__(self) -> None:
            self.calls = 0

        async def prepare(self, **_kwargs: object) -> PreparedRemoteDeployment:
            self.calls += 1
            return PreparedRemoteDeployment(
                deployment_id="d" * 64,
                root=RemotePosixPath("/srv/arena/deployments/deployment"),
                manifest_sha256="a" * 64,
                bundle_sha256="b" * 64,
            )

    manager = _Manager()
    executor = object.__new__(RemoteExecutor)
    executor.worker_bundle = SimpleNamespace(  # type: ignore[assignment]
        manifest=SimpleNamespace(deployment_id="d" * 64)
    )
    executor._transport = _Transport()  # type: ignore[assignment]  # noqa: SLF001
    executor._path_resolver = _Resolver()  # type: ignore[assignment]  # noqa: SLF001
    executor._deployment_manager = manager  # type: ignore[assignment]  # noqa: SLF001
    executor._deployment = None  # noqa: SLF001
    executor._deployment_cache_key = None  # noqa: SLF001

    first = await executor.ensure_deployment("/srv/arena")
    repeated = await executor.ensure_deployment("/srv/arena")
    await executor.ensure_deployment("/srv/other")

    assert repeated is first
    assert manager.calls == 2


@pytest.mark.asyncio
async def test_remote_provisioning_uses_spec_target_paths_without_mutating_spec(
    tmp_path: Path,
) -> None:
    platform = TargetPlatform(operating_system="linux", architecture="x86_64")
    spec = EngineExecutionSpec(
        engine_id="engine-a",
        process=EngineProcessSpec(
            artifact=PlatformArtifactRef(
                logical_id="engine-a-linux",
                kind="engine_binary",
                sha256="a" * 64,
                target_platform=platform,
                entrypoint="engine",
            ),
            working_directory="engines/engine-a-linux",
            handshake_timeout_ms=10_000,
        ),
        usi=EngineUsiSpec(
            path_resources=[
                PathResourceRef(
                    option_values={"EvalDir": "resources/sha256/bb/digest/eval"},
                    artifact=PlatformArtifactRef(
                        logical_id="eval-bb",
                        kind="directory",
                        sha256="b" * 64,
                        target_platform=platform,
                    ),
                    target_relative_path="resources/sha256/bb/digest/eval",
                )
            ]
        ),
    )
    original = spec.model_dump(mode="json")
    calls: list[tuple[str, Path]] = []

    class _Resolver:
        async def expand(self, path: RemotePosixPath) -> RemotePosixPath:
            return RemotePosixPath(str(path).replace("~", "/home/arena", 1))

    class _Store:
        async def ensure(
            self,
            *,
            remote_root: RemotePosixPath,
            reference: PlatformArtifactRef,
            local_path: Path,
        ) -> object:
            assert str(remote_root) == "/home/arena/arena"
            calls.append((reference.logical_id, local_path))
            return SimpleNamespace(path=remote_root / "artifacts" / reference.sha256)

    executor = object.__new__(RemoteExecutor)
    executor._path_resolver = _Resolver()  # type: ignore[assignment]  # noqa: SLF001
    executor._artifact_store = _Store()  # type: ignore[assignment]  # noqa: SLF001
    executor._artifact_paths = {}  # noqa: SLF001

    await executor.provision_game_engine_artifacts(
        remote_root="~/arena",
        spec=spec,
        engine_source=tmp_path / "engine",
        path_sources=(str(tmp_path / "eval"),),
    )

    assert calls == [
        ("engine-a-linux", tmp_path / "engine"),
        ("eval-bb", tmp_path / "eval"),
    ]
    assert str(executor._artifact_paths["engine-a-linux"]).endswith(f"/artifacts/{'a' * 64}")  # noqa: SLF001
    assert str(executor._artifact_paths["eval-bb"]).endswith(f"/artifacts/{'b' * 64}")  # noqa: SLF001
    assert spec.model_dump(mode="json") == original
