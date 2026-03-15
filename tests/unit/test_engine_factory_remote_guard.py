from __future__ import annotations

import pytest

from shogiarena._core.contexts.instances.adapters.engine_runtime_adapter import create_default_engine_runtime_factory
from shogiarena._core.contexts.instances.application.instance_models import Instance, InstanceConfig, InstanceType
from shogiarena._core.platform.engine_provisioning import runtime_factory


@pytest.mark.asyncio
async def test_remote_instances_rejected_on_windows_host(tmp_path, monkeypatch) -> None:
    # Simulate Windows platform on orchestrator host
    monkeypatch.setattr(runtime_factory._platform, "system", lambda: "Windows")

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

    with pytest.raises(RuntimeError) as excinfo:
        await factory._compute_exec_paths(instance, str(local_bin))

    assert "Linux" in str(excinfo.value)
    assert "WSL" in str(excinfo.value)
