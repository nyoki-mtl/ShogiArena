from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from shogiarena._core.contexts.game_session.ports.worker_deployment import (
    WorkerBundleBuildResult,
    WorkerBundleManifest,
)
from shogiarena._core.platform.engine_provisioning.remote_deployment import (
    PreparedRemoteDeployment,
    RemoteDeploymentError,
    RemoteDeploymentManager,
)
from shogiarena._core.platform.engine_provisioning.remote_paths import RemotePosixPath


class _FakeFileTransport:
    def __init__(self, *, prepare_rc: int = 0, platform_matches: bool = True) -> None:
        self.prepare_rc = prepare_rc
        self.platform_matches = platform_matches
        self.is_published = False
        self.is_corrupt = False
        self.is_seal_missing = False
        self.directories: list[str] = []
        self.uploads: list[tuple[Path, str]] = []
        self.commands: list[str] = []

    async def mkdir(self, path: str, *, is_existing_ok: bool = True) -> None:
        del is_existing_ok
        self.directories.append(path)

    async def put_file(self, local: Path, remote: str) -> None:
        self.uploads.append((local, remote))
        await asyncio.sleep(0)

    async def run(
        self,
        command: str,
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        del env, timeout
        self.commands.append(command)
        if "uname -s" in command:
            return (0 if self.platform_matches else 1), "", "wrong platform"
        if "if [ ! -d" in command:
            if not self.is_published:
                return 44, "", ""
            if self.is_seal_missing:
                return 45, "", ""
            if self.is_corrupt:
                return 73, "", "seal mismatch"
            return 0, "", ""
        if "actual=$(sha256sum" in command:
            if self.prepare_rc == 0:
                self.is_published = True
                self.is_seal_missing = False
            return self.prepare_rc, "", "deployment ID content mismatch" if self.prepare_rc else ""
        return 0, "", ""


def _bundle(tmp_path: Path) -> WorkerBundleBuildResult:
    bundle_path = tmp_path / "worker.zip"
    bundle_path.write_bytes(b"bundle")
    manifest = WorkerBundleManifest(
        protocol_version="shogiarena.remote-worker.v1",
        package_version="1.1.0",
        wheel_filename="shogiarena.whl",
        wheel_sha256="1" * 64,
        lock_sha256="2" * 64,
        dependencies_sha256="3" * 64,
        target_os="linux",
        architecture="x86_64",
        python_version="3.12.10",
        uv_version="uv 0.11.29",
        source_policy="snapshot",
        deployment_id="4" * 64,
    )
    return WorkerBundleBuildResult(
        bundle_path=bundle_path,
        bundle_sha256="5" * 64,
        manifest=manifest,
    )


@pytest.mark.asyncio
async def test_prepare_uploads_and_verifies_before_atomic_publish(tmp_path: Path) -> None:
    transport = _FakeFileTransport()
    manager = RemoteDeploymentManager(transport)
    bundle = _bundle(tmp_path)

    prepared = await manager.prepare(remote_root=RemotePosixPath("/srv/arena"), bundle=bundle)

    assert str(prepared.root) == f"/srv/arena/deployments/{'4' * 64}"
    assert len(transport.uploads) == 1
    command = next(command for command in transport.commands if "actual=$(sha256sum" in command)
    assert "bundle digest mismatch" in command
    assert "command -v uv || true" in command
    assert '"$HOME/.local/bin/uv"' in command
    assert command.index('[ -x "$HOME/.local/bin/uv" ]') < command.index("command -v uv || true")
    assert "uv version mismatch" not in command
    assert bundle.manifest.uv_version not in command
    assert '"$remote_uv" run --no-project' in command
    assert "manifest digest mismatch" in command
    assert "wheel digest mismatch" in command
    assert "deployment ID content mismatch" in command
    assert "mv -T" in command
    assert "flock -x" in command
    assert "/metadata/deployments/" in command
    assert "deployment.seal" in command
    assert "stat.S_ISDIR" in command
    assert "git " not in command


@pytest.mark.asyncio
async def test_prepare_rejects_platform_before_staging_or_upload(tmp_path: Path) -> None:
    transport = _FakeFileTransport(platform_matches=False)
    manager = RemoteDeploymentManager(transport)

    with pytest.raises(RemoteDeploymentError, match="remote platform mismatch"):
        await manager.prepare(remote_root=RemotePosixPath("/srv/arena"), bundle=_bundle(tmp_path))

    assert transport.commands == ['test "$(uname -s)" = Linux && test "$(uname -m)" = x86_64']
    assert transport.directories == []
    assert transport.uploads == []


@pytest.mark.asyncio
async def test_concurrent_same_deployment_uses_isolated_staging_paths(tmp_path: Path) -> None:
    transport = _FakeFileTransport()
    manager = RemoteDeploymentManager(transport)
    bundle = _bundle(tmp_path)

    first, second = await asyncio.gather(
        manager.prepare(remote_root=RemotePosixPath("$HOME/arena"), bundle=bundle),
        manager.prepare(remote_root=RemotePosixPath("$HOME/arena"), bundle=bundle),
    )

    assert first.root == second.root
    assert len(set(transport.directories)) == 2
    assert all(path.startswith("$HOME/arena/.staging/") for path in transport.directories)


@pytest.mark.asyncio
async def test_prepare_rejects_same_id_content_mismatch_and_cleans_staging(tmp_path: Path) -> None:
    transport = _FakeFileTransport(prepare_rc=73)
    manager = RemoteDeploymentManager(transport)

    with pytest.raises(RemoteDeploymentError, match="content mismatch"):
        await manager.prepare(remote_root=RemotePosixPath("/srv/arena"), bundle=_bundle(tmp_path))

    assert transport.commands[-1].startswith("rm -rf -- /srv/arena/.staging/")


@pytest.mark.asyncio
async def test_cached_deployment_recomputes_seal_and_rejects_corruption(tmp_path: Path) -> None:
    transport = _FakeFileTransport()
    manager = RemoteDeploymentManager(transport)
    bundle = _bundle(tmp_path)
    await manager.prepare(remote_root=RemotePosixPath("/srv/arena"), bundle=bundle)
    upload_count = len(transport.uploads)
    transport.is_corrupt = True

    with pytest.raises(RemoteDeploymentError, match="integrity verification"):
        await manager.prepare(remote_root=RemotePosixPath("/srv/arena"), bundle=bundle)

    assert len(transport.uploads) == upload_count


@pytest.mark.asyncio
async def test_explicit_reverification_rejects_missing_cached_deployment_without_rebuilding(tmp_path: Path) -> None:
    transport = _FakeFileTransport()
    manager = RemoteDeploymentManager(transport)
    bundle = _bundle(tmp_path)
    deployment = await manager.prepare(remote_root=RemotePosixPath("/srv/arena"), bundle=bundle)
    upload_count = len(transport.uploads)
    transport.is_published = False

    with pytest.raises(RemoteDeploymentError, match="missing its immutable root or seal"):
        await manager.verify(deployment, python_version=bundle.manifest.python_version)

    assert len(transport.uploads) == upload_count


@pytest.mark.asyncio
async def test_missing_external_seal_rebuilds_staging_and_recovers(tmp_path: Path) -> None:
    transport = _FakeFileTransport()
    transport.is_published = True
    transport.is_seal_missing = True
    manager = RemoteDeploymentManager(transport)

    await manager.prepare(remote_root=RemotePosixPath("/srv/arena"), bundle=_bundle(tmp_path))

    assert len(transport.uploads) == 1
    assert transport.is_seal_missing is False


@pytest.mark.asyncio
async def test_gc_dry_run_never_contains_delete_action() -> None:
    transport = _FakeFileTransport()
    manager = RemoteDeploymentManager(transport)

    await manager.collect_garbage(
        remote_root=RemotePosixPath("/srv/arena"),
        minimum_age=timedelta(days=7),
        dry_run=True,
    )

    command = transport.commands[-1]
    assert "threshold=604800" in command
    assert 'rm -rf -- "$deployment"' not in command
    assert '"$meta/pin"' in command
    assert '"$meta/leases"' in command
    assert "flock -x" in command


@pytest.mark.asyncio
async def test_lease_and_gc_serialize_on_same_deployment_lock() -> None:
    transport = _FakeFileTransport()
    manager = RemoteDeploymentManager(transport)
    deployment_id = "4" * 64
    deployment = PreparedRemoteDeployment(
        deployment_id=deployment_id,
        root=RemotePosixPath(f"/srv/arena/deployments/{deployment_id}"),
        manifest_sha256="1" * 64,
        bundle_sha256="2" * 64,
    )

    await manager.acquire_lease(deployment, "job-1")
    lease_command = transport.commands[-1]
    await manager.collect_garbage(
        remote_root=RemotePosixPath("/srv/arena"),
        minimum_age=timedelta(),
        dry_run=False,
    )
    gc_command = transport.commands[-1]

    expected_lock = f"/srv/arena/locks/deployments/{deployment_id}.lock"
    assert expected_lock in lease_command
    assert "/srv/arena/locks/deployments/$id.lock" in gc_command
