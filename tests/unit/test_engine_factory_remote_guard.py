from __future__ import annotations

import pytest

from shogiarena.arena.engines import engine_factory
from shogiarena.arena.instances.models import Instance, InstanceConfig, InstanceType


@pytest.mark.asyncio
async def test_remote_instances_rejected_on_windows_host(tmp_path, monkeypatch) -> None:
    # Simulate Windows platform on orchestrator host
    monkeypatch.setattr(engine_factory.platform, "system", lambda: "Windows")

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

    with pytest.raises(RuntimeError) as excinfo:
        await engine_factory.EngineFactory._compute_exec_paths(instance, str(local_bin))

    assert "Linux" in str(excinfo.value)
    assert "WSL" in str(excinfo.value)
