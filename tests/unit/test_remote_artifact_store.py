from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    PlatformArtifactRef,
    TargetPlatform,
)
from shogiarena._core.contexts.game_session.ports.worker_deployment import WORKER_PYTHON_VERSION
from shogiarena._core.platform.engine_provisioning.remote_artifact_store import (
    RemoteArtifactIntegrityError,
    RemoteArtifactStore,
    _build_deterministic_tar,
    _remote_tree_digest_command,
)
from shogiarena._core.platform.engine_provisioning.remote_paths import RemotePosixPath
from shogiarena._core.shared.kernel.content_hashing import (
    CANONICAL_ARTIFACT_DIRECTORY_MODE,
    CANONICAL_ARTIFACT_FILE_MODE,
    sha256_file,
    sha256_tree,
)
from shogiarena._core.shared.kernel.remote_provisioning import (
    remote_provisioning_scope,
    resolve_remote_provisioning_policy,
)


class _CasTransport:
    def __init__(self, endpoint: str, *, platform_matches: bool = True) -> None:
        self.endpoint = endpoint
        self.platform_matches = platform_matches
        self.has_object = False
        self.uploads: list[str] = []
        self.commands: list[str] = []

    async def endpoint_identity(self) -> str:
        return self.endpoint

    async def mkdir(self, path: str, *, is_existing_ok: bool = True) -> None:
        del path, is_existing_ok

    async def put_file(self, local: Path, remote: str) -> None:
        del local
        self.uploads.append(remote)

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
        if "mv -T" in command:
            self.has_object = True
            return 0, "", ""
        if command.startswith("test ! -L") or command.startswith("test -d"):
            return (0 if self.has_object else 1), "", ""
        return 0, "", ""


def _file_ref(path: Path, *, digest: str | None = None) -> PlatformArtifactRef:
    return PlatformArtifactRef(
        logical_id="engine",
        kind="engine_binary",
        sha256=digest or sha256_file(path),
        target_platform=TargetPlatform(operating_system="linux", architecture="x86_64"),
        entrypoint=path.name,
    )


def _directory_ref(path: Path) -> PlatformArtifactRef:
    return PlatformArtifactRef(
        logical_id="eval",
        kind="directory",
        sha256=sha256_tree(path),
        target_platform=TargetPlatform(operating_system="linux", architecture="x86_64"),
    )


@pytest.mark.asyncio
async def test_same_endpoint_digest_skips_but_different_endpoint_does_not(tmp_path: Path) -> None:
    binary = tmp_path / "engine"
    binary.write_bytes(b"engine")
    reference = _file_ref(binary)
    first_transport = _CasTransport("ssh://u@host-a:22?host_key=SHA256:a")
    second_transport = _CasTransport("ssh://u@host-b:22?host_key=SHA256:b")

    first_store = RemoteArtifactStore(first_transport)
    first = await first_store.ensure(
        remote_root=RemotePosixPath("/srv/arena"),
        reference=reference,
        local_path=binary,
    )
    repeated = await first_store.ensure(
        remote_root=RemotePosixPath("/srv/arena"),
        reference=reference,
        local_path=binary,
    )
    second = await RemoteArtifactStore(second_transport).ensure(
        remote_root=RemotePosixPath("/srv/arena"),
        reference=reference,
        local_path=binary,
    )

    assert first.was_transferred is True
    assert repeated.was_transferred is False
    assert second.was_transferred is True
    assert len(first_transport.uploads) == 1
    assert len(second_transport.uploads) == 1
    assert first.cache_key != second.cache_key
    assert str(first.path).endswith(f"/engine_binary/sha256/{reference.sha256[:2]}/{reference.sha256}/content")


@pytest.mark.asyncio
async def test_local_digest_mismatch_stops_before_remote_dispatch(tmp_path: Path) -> None:
    binary = tmp_path / "engine"
    binary.write_bytes(b"engine")
    transport = _CasTransport("ssh://u@host:22?host_key=SHA256:a")

    with pytest.raises(RemoteArtifactIntegrityError, match="local artifact digest mismatch"):
        await RemoteArtifactStore(transport).ensure(
            remote_root=RemotePosixPath("/srv/arena"),
            reference=_file_ref(binary, digest="0" * 64),
            local_path=binary,
        )

    assert transport.commands == []
    assert transport.uploads == []


def test_directory_archive_bytes_are_deterministic(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "b.txt").write_text("b", encoding="utf-8")
    (tree / "a.txt").write_text("a", encoding="utf-8")

    first = _build_deterministic_tar(tree)
    second = _build_deterministic_tar(tree)
    try:
        assert sha256_file(first) == sha256_file(second)
        with tarfile.open(first) as archive:
            members = {member.name: member for member in archive.getmembers()}
        assert members["a.txt"].mode == CANONICAL_ARTIFACT_FILE_MODE
        assert members["b.txt"].mode == CANONICAL_ARTIFACT_FILE_MODE
    finally:
        first.unlink(missing_ok=True)
        second.unlink(missing_ok=True)


def test_remote_tree_digest_requires_canonical_modes() -> None:
    command = _remote_tree_digest_command(RemotePosixPath("/srv/tree"), "0" * 64)

    assert f"expected_mode = {CANONICAL_ARTIFACT_DIRECTORY_MODE}" in command
    assert f"expected_mode = {CANONICAL_ARTIFACT_FILE_MODE}" in command
    assert "artifact tree mode mismatch" in command
    assert "$HOME/.local/bin/uv" in command
    assert '"$remote_uv" run' in command
    assert f"--python {WORKER_PYTHON_VERSION}" in command


@pytest.mark.asyncio
async def test_directory_upload_normalizes_linux_modes_before_digest(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / "nested").mkdir(parents=True)
    (tree / "nested" / "weights.bin").write_bytes(b"weights")
    transport = _CasTransport("ssh://u@host:22?host_key=SHA256:a")

    await RemoteArtifactStore(transport).ensure(
        remote_root=RemotePosixPath("/srv/arena"),
        reference=_directory_ref(tree),
        local_path=tree,
    )

    upload_command = next(command for command in transport.commands if "tar -xf" in command)
    assert f"-type d -exec chmod {CANONICAL_ARTIFACT_DIRECTORY_MODE:o}" in upload_command
    assert f"-type f -exec chmod {CANONICAL_ARTIFACT_FILE_MODE:o}" in upload_command


def test_directory_digest_and_archive_reject_symlink_directory(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    target = tmp_path / "target"
    tree.mkdir()
    target.mkdir()
    try:
        (tree / "linked").symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    with pytest.raises(ValueError, match="must not contain symlinks"):
        sha256_tree(tree)
    with pytest.raises(ValueError, match="must not contain symlinks"):
        _build_deterministic_tar(tree)


@pytest.mark.asyncio
async def test_preplaced_policy_verifies_declared_remote_path_without_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "engine"
    binary.write_bytes(b"engine")
    reference = _file_ref(binary)
    transport = _CasTransport("ssh://u@host:22?host_key=SHA256:a")
    transport.has_object = True
    monkeypatch.setenv(
        "SHOGIARENA_REMOTE_PREPLACED_RESOURCES",
        ('{"engine":{"path":"/opt/engines/engine","sha256":"' + reference.sha256 + '"}}'),
    )

    with remote_provisioning_scope("preplaced"):
        artifact = await RemoteArtifactStore(transport).ensure(
            remote_root=RemotePosixPath("/srv/arena"),
            reference=reference,
            local_path=binary,
        )

    assert str(artifact.path) == "/opt/engines/engine"
    assert artifact.was_transferred is False
    assert transport.uploads == []


@pytest.mark.asyncio
async def test_remote_platform_mismatch_stops_before_upload(tmp_path: Path) -> None:
    binary = tmp_path / "engine"
    binary.write_bytes(b"engine")
    transport = _CasTransport("ssh://u@host:22?host_key=SHA256:a", platform_matches=False)

    with pytest.raises(RemoteArtifactIntegrityError, match="remote platform mismatch"):
        await RemoteArtifactStore(transport).ensure(
            remote_root=RemotePosixPath("/srv/arena"),
            reference=_file_ref(binary),
            local_path=binary,
        )

    assert transport.uploads == []


@pytest.mark.asyncio
async def test_remote_file_verification_rejects_symlink_path(tmp_path: Path) -> None:
    binary = tmp_path / "engine"
    binary.write_bytes(b"engine")
    transport = _CasTransport("ssh://u@host:22?host_key=SHA256:a")

    await RemoteArtifactStore(transport).ensure(
        remote_root=RemotePosixPath("/srv/arena"),
        reference=_file_ref(binary),
        local_path=binary,
    )

    verification = next(command for command in transport.commands if "sha256sum" in command)
    assert verification.startswith("test ! -L ")


def test_preplaced_policy_requires_resource_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHOGIARENA_REMOTE_PREPLACED_RESOURCES", raising=False)

    with pytest.raises(ValueError, match="requires SHOGIARENA_REMOTE_PREPLACED_RESOURCES"):
        with remote_provisioning_scope("preplaced"):
            raise AssertionError("scope must reject before entry")


def test_preplaced_policy_can_be_resolved_before_scope_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHOGIARENA_REMOTE_PREPLACED_RESOURCES", raising=False)

    with pytest.raises(ValueError, match="requires SHOGIARENA_REMOTE_PREPLACED_RESOURCES"):
        resolve_remote_provisioning_policy("preplaced")
