from __future__ import annotations

import pytest

from shogiarena._core.contexts.instances.adapters.engine_runtime_adapter import create_default_engine_runtime_factory
from shogiarena._core.contexts.instances.application.instance_models import Instance, InstanceConfig, InstanceType


@pytest.mark.asyncio
async def test_remote_exec_paths_use_posix_semantics_on_coordinator_host(tmp_path, monkeypatch) -> None:
    local_bin_dir = tmp_path / "engines" / "test"
    local_bin_dir.mkdir(parents=True)
    local_bin = local_bin_dir / "engine"
    local_bin.write_bytes(b"\x7fELF")

    cfg = InstanceConfig(
        name="remote",
        type=InstanceType.SSH,
        engine_dir="",
        project_root="/home/remote/ShogiArena-remote",
        host="example.com",
        user="arena",
        port=22,
        slots=1,
    )
    instance = Instance(config=cfg)

    factory = create_default_engine_runtime_factory()
    transferred: list[tuple[object, object, str]] = []

    async def ensure_remote_binary(
        target_instance: object,
        local_binary: object,
        remote_binary: str,
    ) -> None:
        transferred.append((target_instance, local_binary, remote_binary))

    monkeypatch.setattr(factory._support, "ensure_remote_binary", ensure_remote_binary)  # noqa: SLF001

    engine_path, working_directory = await factory._compute_exec_paths(instance, str(local_bin))

    assert engine_path == "/home/remote/ShogiArena-remote/data/engines/test/engine"
    assert working_directory == "/home/remote/ShogiArena-remote/data/engines/test"
    assert transferred == [(instance, local_bin, engine_path)]
