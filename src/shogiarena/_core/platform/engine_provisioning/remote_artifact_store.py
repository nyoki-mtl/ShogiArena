"""Endpoint-aware content-addressed artifact storage."""

from __future__ import annotations

import io
import shlex
import stat
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from shogiarena._core.contexts.game_session.ports.game_execution_spec import PlatformArtifactRef
from shogiarena._core.contexts.game_session.ports.worker_deployment import WORKER_PYTHON_VERSION
from shogiarena._core.platform.engine_provisioning.provisioning_ports import SshFileTransportPort
from shogiarena._core.platform.engine_provisioning.remote_paths import RemotePosixPath
from shogiarena._core.platform.engine_provisioning.remote_uv import REMOTE_UV_DISCOVERY_SCRIPT
from shogiarena._core.shared.kernel.content_hashing import (
    CANONICAL_ARTIFACT_DIRECTORY_MODE,
    CANONICAL_ARTIFACT_FILE_MODE,
    sha256_file,
    sha256_tree,
)
from shogiarena._core.shared.kernel.remote_provisioning import current_remote_provisioning_policy


class RemoteArtifactIntegrityError(RuntimeError):
    """Artifact bytes do not match the declared content identity."""


@dataclass(frozen=True, slots=True)
class RemoteArtifact:
    """Verified artifact placement."""

    endpoint_identity: str
    reference: PlatformArtifactRef
    path: RemotePosixPath
    was_transferred: bool

    @property
    def cache_key(self) -> tuple[str, str, str, str]:
        """Endpoint/platform/digest/kind cache keyを返す。"""

        platform = self.reference.target_platform
        return (
            self.endpoint_identity,
            f"{platform.operating_system}-{platform.architecture}",
            self.reference.sha256,
            self.reference.kind,
        )


class RemoteArtifactStore:
    """Verify and place immutable artifacts in an endpoint-scoped CAS."""

    def __init__(self, transport: SshFileTransportPort) -> None:
        self._transport = transport

    async def ensure(
        self,
        *,
        remote_root: RemotePosixPath,
        reference: PlatformArtifactRef,
        local_path: Path,
    ) -> RemoteArtifact:
        """Ensure one file or directory under its content digest."""

        platform = reference.target_platform
        if platform.operating_system != "linux":
            raise ValueError("remote artifact store only supports linux targets")
        actual_digest = sha256_tree(local_path) if reference.kind == "directory" else sha256_file(local_path)
        if actual_digest != reference.sha256:
            raise RemoteArtifactIntegrityError(
                f"local artifact digest mismatch for {reference.logical_id}: "
                f"expected {reference.sha256}, got {actual_digest}"
            )
        await self._verify_remote_platform(reference)
        endpoint = await self._transport.endpoint_identity()
        policy = current_remote_provisioning_policy()
        if policy.mode == "preplaced":
            placement = policy.preplaced_resources.get(reference.logical_id)
            if placement is None:
                raise RemoteArtifactIntegrityError(
                    f"preplaced resource contract is missing logical ID: {reference.logical_id}"
                )
            if placement.sha256 != reference.sha256:
                raise RemoteArtifactIntegrityError(
                    f"preplaced expected digest does not match spec for {reference.logical_id}: "
                    f"{placement.sha256} != {reference.sha256}"
                )
            preplaced_path = RemotePosixPath(placement.remote_path)
            if not await self._remote_matches(preplaced_path, reference):
                raise RemoteArtifactIntegrityError(
                    f"preplaced resource does not exist or failed remote hash/mode verification: "
                    f"{reference.logical_id} at {preplaced_path}"
                )
            return RemoteArtifact(endpoint, reference, preplaced_path, False)
        object_root = (
            remote_root
            / "artifacts"
            / f"{platform.operating_system}-{platform.architecture}"
            / reference.kind
            / "sha256"
            / reference.sha256[:2]
            / reference.sha256
        )
        content_path = object_root / "content"
        if await self._remote_matches(content_path, reference):
            return RemoteArtifact(endpoint, reference, content_path, False)
        if reference.kind == "directory":
            await self._upload_directory(local_path, object_root, reference)
        else:
            await self._upload_file(local_path, object_root, reference)
        if not await self._remote_matches(content_path, reference):
            raise RemoteArtifactIntegrityError(f"remote artifact verification failed: {reference.logical_id}")
        return RemoteArtifact(endpoint, reference, content_path, True)

    async def _remote_matches(self, content_path: RemotePosixPath, reference: PlatformArtifactRef) -> bool:
        if reference.kind != "directory":
            executable_check = " && test -x " + content_path.shell_quote() if reference.kind == "engine_binary" else ""
            command = (
                f"test ! -L {content_path.shell_quote()} && test -f {content_path.shell_quote()} && "
                f"test \"$(sha256sum {content_path.shell_quote()} | cut -d' ' -f1)\" = "
                f"{shlex.quote(reference.sha256)}{executable_check}"
            )
        else:
            command = _remote_tree_digest_command(content_path, reference.sha256)
        rc, _, _ = await self._transport.run(command)
        return rc == 0

    async def _verify_remote_platform(self, reference: PlatformArtifactRef) -> None:
        platform = reference.target_platform
        command = f'test "$(uname -s)" = Linux && test "$(uname -m)" = {shlex.quote(platform.architecture)}'
        rc, stdout, stderr = await self._transport.run(command)
        if rc != 0:
            detail = stderr.strip() or stdout.strip() or "remote uname does not match sealed target platform"
            raise RemoteArtifactIntegrityError(
                f"remote platform mismatch for {reference.logical_id}: "
                f"expected {platform.operating_system}/{platform.architecture}: {detail}"
            )

    async def _upload_file(
        self,
        local_path: Path,
        object_root: RemotePosixPath,
        reference: PlatformArtifactRef,
    ) -> None:
        staging = object_root.parent / f".{reference.sha256}.{uuid.uuid4().hex}.tmp"
        await self._transport.mkdir(str(staging), is_existing_ok=False)
        uploaded = staging / "content"
        try:
            await self._transport.put_file(local_path, str(uploaded))
            chmod = f"chmod 755 {uploaded.shell_quote()}; " if reference.kind == "engine_binary" else ""
            command = (
                f"set -eu; {chmod}"
                f"test \"$(sha256sum {uploaded.shell_quote()} | cut -d' ' -f1)\" = "
                f"{shlex.quote(reference.sha256)}; "
                f"mkdir -p {object_root.parent.shell_quote()}; "
                f"mv -T {staging.shell_quote()} {object_root.shell_quote()} 2>/dev/null "
                f"|| rm -rf -- {staging.shell_quote()}"
            )
            await self._run_checked(command)
        except BaseException:
            await self._transport.run(f"rm -rf -- {staging.shell_quote()}")
            raise

    async def _upload_directory(
        self,
        local_path: Path,
        object_root: RemotePosixPath,
        reference: PlatformArtifactRef,
    ) -> None:
        archive_path = _build_deterministic_tar(local_path)
        archive_digest = sha256_file(archive_path)
        staging = object_root.parent / f".{reference.sha256}.{uuid.uuid4().hex}.tmp"
        remote_archive = staging / "content.tar"
        content = staging / "content"
        await self._transport.mkdir(str(staging), is_existing_ok=False)
        try:
            await self._transport.put_file(archive_path, str(remote_archive))
            command = (
                "set -eu; "
                f"test \"$(sha256sum {remote_archive.shell_quote()} | cut -d' ' -f1)\" = "
                f"{shlex.quote(archive_digest)}; "
                f"mkdir -p {content.shell_quote()}; tar -xf {remote_archive.shell_quote()} "
                f"-C {content.shell_quote()}; "
                f"find {content.shell_quote()} -type d -exec chmod "
                f"{CANONICAL_ARTIFACT_DIRECTORY_MODE:o} {{}} +; "
                f"find {content.shell_quote()} -type f -exec chmod "
                f"{CANONICAL_ARTIFACT_FILE_MODE:o} {{}} +; "
                f"rm -f -- {remote_archive.shell_quote()}; "
                f"{_remote_tree_digest_command(content, reference.sha256)}; "
                f"mkdir -p {object_root.parent.shell_quote()}; "
                f"mv -T {staging.shell_quote()} {object_root.shell_quote()} 2>/dev/null "
                f"|| rm -rf -- {staging.shell_quote()}"
            )
            await self._run_checked(command)
        except BaseException:
            await self._transport.run(f"rm -rf -- {staging.shell_quote()}")
            raise
        finally:
            archive_path.unlink(missing_ok=True)

    async def _run_checked(self, command: str) -> None:
        rc, stdout, stderr = await self._transport.run(command)
        if rc != 0:
            raise RemoteArtifactIntegrityError(stderr.strip() or stdout.strip() or "remote artifact command failed")


def _build_deterministic_tar(root: Path) -> Path:
    if root.is_symlink():
        raise ValueError(f"Artifact tree root must not be a symlink: {root}")
    with tempfile.NamedTemporaryFile(prefix="shogiarena-cas-", suffix=".tar", delete=False) as handle:
        archive_path = Path(handle.name)
    try:
        with tarfile.open(archive_path, "w") as archive:
            for child in sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix()):
                mode = child.lstat().st_mode
                if stat.S_ISLNK(mode):
                    raise ValueError(f"Artifact tree must not contain symlinks: {child}")
                if not stat.S_ISDIR(mode) and not stat.S_ISREG(mode):
                    raise ValueError(f"Artifact tree contains unsupported entry: {child}")
                relative = child.relative_to(root).as_posix()
                info = tarfile.TarInfo(relative)
                info.mtime = 0
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                if stat.S_ISDIR(mode):
                    info.type = tarfile.DIRTYPE
                    info.mode = CANONICAL_ARTIFACT_DIRECTORY_MODE
                    archive.addfile(info)
                    continue
                payload = child.read_bytes()
                info.size = len(payload)
                info.mode = CANONICAL_ARTIFACT_FILE_MODE
                archive.addfile(info, io.BytesIO(payload))
    except BaseException:
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path


def _remote_tree_digest_command(content_path: RemotePosixPath, expected_digest: str) -> str:
    script = (
        """
import hashlib
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1])
if root.is_symlink():
    raise SystemExit("artifact tree root is a symlink")
digest = hashlib.sha256()
for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
    relative = path.relative_to(root).as_posix()
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode):
        raise SystemExit(f"artifact tree contains symlink: {relative}")
    if stat.S_ISDIR(mode):
        kind = b"D"
        payload = b""
        expected_mode = __DIRECTORY_MODE__
    elif stat.S_ISREG(mode):
        kind = b"F"
        payload = path.read_bytes()
        expected_mode = __FILE_MODE__
    else:
        raise SystemExit(f"artifact tree contains unsupported entry: {relative}")
    if stat.S_IMODE(mode) != expected_mode:
        raise SystemExit(f"artifact tree mode mismatch: {relative}")
    digest.update(relative.encode("utf-8"))
    digest.update(b"\\0")
    digest.update(kind)
    digest.update(b"\\0")
    digest.update(oct(expected_mode).encode("ascii"))
    digest.update(b"\\0")
    digest.update(str(len(payload)).encode("ascii"))
    digest.update(b"\\0")
    digest.update(hashlib.sha256(payload).hexdigest().encode("ascii"))
    digest.update(b"\\n")
print(digest.hexdigest())
""".strip()
        .replace("__DIRECTORY_MODE__", str(CANONICAL_ARTIFACT_DIRECTORY_MODE))
        .replace(
            "__FILE_MODE__",
            str(CANONICAL_ARTIFACT_FILE_MODE),
        )
    )
    return (
        f"test -d {content_path.shell_quote()} && ("
        f"{REMOTE_UV_DISCOVERY_SCRIPT}; "
        f'test "$("$remote_uv" run --no-project --python {shlex.quote(WORKER_PYTHON_VERSION)} python -c '
        f'{shlex.quote(script)} {content_path.shell_quote()})" = '
        f"{shlex.quote(expected_digest)})"
    )


__all__ = [
    "RemoteArtifact",
    "RemoteArtifactIntegrityError",
    "RemoteArtifactStore",
]
