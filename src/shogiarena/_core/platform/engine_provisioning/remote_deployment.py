"""Immutable worker deployment preparation and retention."""

from __future__ import annotations

import shlex
import uuid
from dataclasses import dataclass
from datetime import timedelta

from shogiarena._core.contexts.game_session.ports.worker_deployment import WorkerBundleBuildResult
from shogiarena._core.platform.engine_provisioning.provisioning_ports import SshFileTransportPort
from shogiarena._core.platform.engine_provisioning.remote_paths import RemotePosixPath
from shogiarena._core.platform.engine_provisioning.remote_uv import REMOTE_UV_DISCOVERY_SCRIPT


class RemoteDeploymentError(RuntimeError):
    """Remote deployment integrity contract violation."""


@dataclass(frozen=True, slots=True)
class PreparedRemoteDeployment:
    """Verified immutable remote deployment."""

    deployment_id: str
    root: RemotePosixPath
    manifest_sha256: str
    bundle_sha256: str


class RemoteDeploymentManager:
    """Install content-addressed worker bundles without a shared checkout."""

    def __init__(self, transport: SshFileTransportPort) -> None:
        self._transport = transport

    async def prepare(
        self,
        *,
        remote_root: RemotePosixPath,
        bundle: WorkerBundleBuildResult,
    ) -> PreparedRemoteDeployment:
        """Upload, verify, install, and atomically publish one deployment."""

        await self._verify_remote_platform(
            target_os=bundle.manifest.target_os,
            architecture=bundle.manifest.architecture,
        )
        deployment_id = bundle.manifest.deployment_id
        deployments_root = remote_root / "deployments"
        final_root = deployments_root / deployment_id
        metadata_root = remote_root / "metadata" / "deployments" / deployment_id
        existing_status = await self._verify_existing(
            final_root=final_root,
            metadata_root=metadata_root,
            manifest_sha256=bundle.manifest.manifest_sha256,
            python_version=bundle.manifest.python_version,
        )
        if existing_status:
            return PreparedRemoteDeployment(
                deployment_id=deployment_id,
                root=final_root,
                manifest_sha256=bundle.manifest.manifest_sha256,
                bundle_sha256=bundle.bundle_sha256,
            )
        staging_root = remote_root / ".staging" / f"{deployment_id}.{uuid.uuid4().hex}"
        remote_bundle = staging_root / "bundle.zip"
        await self._transport.mkdir(str(staging_root), is_existing_ok=False)
        try:
            await self._transport.put_file(bundle.bundle_path, str(remote_bundle))
            command = self._prepare_command(
                staging_root=staging_root,
                final_root=final_root,
                metadata_root=metadata_root,
                bundle=bundle,
            )
            rc, stdout, stderr = await self._transport.run(command)
            if rc != 0:
                detail = stderr.strip() or stdout.strip() or f"exit {rc}"
                raise RemoteDeploymentError(f"remote deployment prepare failed: {detail}")
            await self._verify_existing(
                final_root=final_root,
                metadata_root=metadata_root,
                manifest_sha256=bundle.manifest.manifest_sha256,
                python_version=bundle.manifest.python_version,
            )
        except BaseException:
            await self._cleanup_staging(staging_root)
            raise
        return PreparedRemoteDeployment(
            deployment_id=deployment_id,
            root=final_root,
            manifest_sha256=bundle.manifest.manifest_sha256,
            bundle_sha256=bundle.bundle_sha256,
        )

    async def verify(
        self,
        deployment: PreparedRemoteDeployment,
        *,
        python_version: str,
    ) -> None:
        """Worker control失敗後にprepared deploymentを再検証する。"""

        metadata_root = deployment.root.parent.parent / "metadata" / "deployments" / deployment.deployment_id
        is_valid = await self._verify_existing(
            final_root=deployment.root,
            metadata_root=metadata_root,
            manifest_sha256=deployment.manifest_sha256,
            python_version=python_version,
        )
        if not is_valid:
            raise RemoteDeploymentError(
                f"prepared remote deployment is missing its immutable root or seal: {deployment.deployment_id}"
            )

    async def _verify_existing(
        self,
        *,
        final_root: RemotePosixPath,
        metadata_root: RemotePosixPath,
        manifest_sha256: str,
        python_version: str,
    ) -> bool:
        lock_path = final_root.parent.parent / "locks" / "deployments" / f"{final_root.name}.lock"
        expected_seal_path = metadata_root / "deployment.seal"
        verify_script = (
            f"if [ ! -d {final_root.shell_quote()} ]; then exit 44; fi; "
            f"{REMOTE_UV_DISCOVERY_SCRIPT}; "
            f"existing_manifest=$(sha256sum {(final_root / 'manifest.json').shell_quote()} "
            "2>/dev/null | cut -d' ' -f1 || true); "
            f'[ "$existing_manifest" = {shlex.quote(manifest_sha256)} ] || exit 73; '
            f"expected_seal=$(cat {expected_seal_path.shell_quote()} 2>/dev/null || true); "
            '[ -n "$expected_seal" ] || exit 45; '
            f'actual_seal=$("$remote_uv" run --no-project --python {shlex.quote(python_version)} '
            f"python -c {shlex.quote(_DEPLOYMENT_SEAL_SCRIPT)} "
            f"{final_root.shell_quote()} 2>/dev/null || true); "
            '[ "$actual_seal" = "$expected_seal" ] || exit 73; '
            f"mkdir -p {metadata_root.shell_quote()}; date +%s > {(metadata_root / 'last-used').shell_quote()}"
        )
        command = _locked_command(lock_path, verify_script)
        rc, stdout, stderr = await self._transport.run(command)
        if rc in {44, 45}:
            return False
        if rc != 0:
            raise RemoteDeploymentError(
                f"existing deployment failed integrity verification: {stderr.strip() or stdout.strip() or rc}"
            )
        return True

    async def set_pin(self, deployment: PreparedRemoteDeployment, *, is_pinned: bool) -> None:
        """Deployment GC pinを更新する。"""

        metadata = deployment.root.parent.parent / "metadata" / "deployments" / deployment.deployment_id
        lock = deployment.root.parent.parent / "locks" / "deployments" / f"{deployment.deployment_id}.lock"
        pin = metadata / "pin"
        script = (
            f"mkdir -p {metadata.shell_quote()}; : > {pin.shell_quote()}"
            if is_pinned
            else f"rm -f -- {pin.shell_quote()}"
        )
        await self._run_checked(_locked_command(lock, script), action="update deployment pin")

    async def acquire_lease(self, deployment: PreparedRemoteDeployment, lease_id: str) -> None:
        """Active job leaseを作成する。"""

        _validate_token(lease_id, field="lease_id")
        metadata = deployment.root.parent.parent / "metadata" / "deployments" / deployment.deployment_id
        lock = deployment.root.parent.parent / "locks" / "deployments" / f"{deployment.deployment_id}.lock"
        lease = metadata / "leases" / lease_id
        await self._run_checked(
            _locked_command(
                lock,
                f"test -d {deployment.root.shell_quote()}; mkdir -p {lease.parent.shell_quote()}; "
                f": > {lease.shell_quote()}; date +%s > {(metadata / 'last-used').shell_quote()}",
            ),
            action="acquire deployment lease",
        )

    async def release_lease(self, deployment: PreparedRemoteDeployment, lease_id: str) -> None:
        """Active job leaseを解放する。"""

        _validate_token(lease_id, field="lease_id")
        metadata = deployment.root.parent.parent / "metadata" / "deployments" / deployment.deployment_id
        lock = deployment.root.parent.parent / "locks" / "deployments" / f"{deployment.deployment_id}.lock"
        lease = metadata / "leases" / lease_id
        await self._run_checked(
            _locked_command(
                lock,
                f"rm -f -- {lease.shell_quote()}; "
                f"mkdir -p {metadata.shell_quote()}; date +%s > {(metadata / 'last-used').shell_quote()}",
            ),
            action="release deployment lease",
        )

    async def collect_garbage(
        self,
        *,
        remote_root: RemotePosixPath,
        minimum_age: timedelta,
        dry_run: bool,
    ) -> tuple[str, ...]:
        """Unpinned、unleased、age超過deploymentだけを列挙または削除する。"""

        if minimum_age.total_seconds() < 0:
            raise ValueError("minimum_age must be non-negative")
        deployments = remote_root / "deployments"
        metadata = remote_root / "metadata" / "deployments"
        locks = remote_root / "locks" / "deployments"
        action = "printf '%s\\n' \"$deployment\""
        if not dry_run:
            action = 'rm -rf -- "$deployment" "$meta"; printf \'%s\\n\' "$deployment"'
        gc_inner = (
            "deployment=$1; meta=$2; now=$3; threshold=$4; "
            '[ ! -f "$meta/pin" ] || exit 0; '
            '[ ! -d "$meta/leases" ] || [ -z "$(find "$meta/leases" -type f -print -quit)" ] || exit 0; '
            'last=$(cat "$meta/last-used" 2>/dev/null || printf 0); '
            '[ $((now-last)) -ge "$threshold" ] || exit 0; ' + action
        )
        script = (
            f"set -eu; mkdir -p {locks.shell_quote()}; now=$(date +%s); "
            f"threshold={int(minimum_age.total_seconds())}; "
            f"for deployment in {deployments.shell_quote()}/*; do "
            '[ -d "$deployment" ] || continue; id=${deployment##*/}; '
            f"meta={metadata.shell_quote()}/$id; "
            f"lock={locks.shell_quote()}/$id.lock; "
            f'flock -x "$lock" bash -c {shlex.quote(gc_inner)} _ '
            '"$deployment" "$meta" "$now" "$threshold"; done'
        )
        rc, stdout, stderr = await self._transport.run(script)
        if rc != 0:
            raise RemoteDeploymentError(f"remote deployment GC failed: {stderr.strip() or stdout.strip()}")
        return tuple(line for line in stdout.splitlines() if line)

    def _prepare_command(
        self,
        *,
        staging_root: RemotePosixPath,
        final_root: RemotePosixPath,
        metadata_root: RemotePosixPath,
        bundle: WorkerBundleBuildResult,
    ) -> str:
        manifest_digest = bundle.manifest.manifest_sha256
        bundle_path = staging_root / "bundle.zip"
        extract_root = staging_root / "worker"
        extracted_manifest = extract_root / "manifest.json"
        deployment_manifest = staging_root / "manifest.json"
        final_manifest = final_root / "manifest.json"
        requirements_path = extract_root / "requirements.lock"
        python_path = staging_root / "venv" / "bin" / "python"
        seal_path = staging_root / "deployment.seal"
        wheel_path = staging_root / "worker" / "worker" / bundle.manifest.wheel_filename
        parts = [
            "set -eu",
            REMOTE_UV_DISCOVERY_SCRIPT,
            f"actual=$(sha256sum {bundle_path.shell_quote()} | cut -d' ' -f1)",
            f'[ "$actual" = {shlex.quote(bundle.bundle_sha256)} ] || {{ echo "bundle digest mismatch" >&2; exit 71; }}',
            f"mkdir -p {extract_root.shell_quote()}",
            f'"$remote_uv" run --no-project --python {shlex.quote(bundle.manifest.python_version)} '
            "python -m zipfile -e "
            f"{bundle_path.shell_quote()} {extract_root.shell_quote()}",
            f"actual_manifest=$(sha256sum {extracted_manifest.shell_quote()} | cut -d' ' -f1)",
            f'[ "$actual_manifest" = {shlex.quote(manifest_digest)} ] || '
            '{ echo "manifest digest mismatch" >&2; exit 72; }',
            f"actual_wheel=$(sha256sum {wheel_path.shell_quote()} | cut -d' ' -f1)",
            f'[ "$actual_wheel" = {shlex.quote(bundle.manifest.wheel_sha256)} ] || '
            '{ echo "wheel digest mismatch" >&2; exit 72; }',
            f'"$remote_uv" venv --relocatable --python {shlex.quote(bundle.manifest.python_version)} '
            f"{(staging_root / 'venv').shell_quote()}",
            f'"$remote_uv" pip install --python {python_path.shell_quote()} '
            f"-r {requirements_path.shell_quote()} {wheel_path.shell_quote()}",
            f"cp {extracted_manifest.shell_quote()} {deployment_manifest.shell_quote()}",
            f'"$remote_uv" run --no-project --python {shlex.quote(bundle.manifest.python_version)} '
            f"python -c {shlex.quote(_DEPLOYMENT_SEAL_SCRIPT)} "
            f"{staging_root.shell_quote()} > {seal_path.shell_quote()}",
            _publish_command(
                staging_root=staging_root,
                final_root=final_root,
                final_manifest=final_manifest,
                metadata_root=metadata_root,
                manifest_digest=manifest_digest,
                python_version=bundle.manifest.python_version,
            ),
        ]
        return "; ".join(parts)

    async def _cleanup_staging(self, staging_root: RemotePosixPath) -> None:
        await self._transport.run(f"rm -rf -- {staging_root.shell_quote()}")

    async def _verify_remote_platform(self, *, target_os: str, architecture: str) -> None:
        expected_system = "Linux" if target_os == "linux" else target_os
        command = (
            f'test "$(uname -s)" = {shlex.quote(expected_system)} && test "$(uname -m)" = {shlex.quote(architecture)}'
        )
        rc, stdout, stderr = await self._transport.run(command)
        if rc != 0:
            detail = stderr.strip() or stdout.strip() or "remote uname does not match worker bundle"
            raise RemoteDeploymentError(f"remote platform mismatch: expected {target_os}/{architecture}: {detail}")

    async def _run_checked(self, command: str, *, action: str) -> None:
        rc, stdout, stderr = await self._transport.run(command)
        if rc != 0:
            raise RemoteDeploymentError(f"failed to {action}: {stderr.strip() or stdout.strip()}")


def _validate_token(value: str, *, field: str) -> None:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if not value or any(character not in allowed for character in value):
        raise ValueError(f"{field} contains unsupported characters")


def _locked_command(lock_path: RemotePosixPath, script: str) -> str:
    return (
        f"mkdir -p {lock_path.parent.shell_quote()}; flock -x {lock_path.shell_quote()} bash -c {shlex.quote(script)}"
    )


def _publish_command(
    *,
    staging_root: RemotePosixPath,
    final_root: RemotePosixPath,
    final_manifest: RemotePosixPath,
    metadata_root: RemotePosixPath,
    manifest_digest: str,
    python_version: str,
) -> str:
    lock_path = final_root.parent.parent / "locks" / "deployments" / f"{final_root.name}.lock"
    staging_seal = staging_root / "deployment.seal"
    expected_seal_path = metadata_root / "deployment.seal"
    seal_temp = metadata_root / "deployment.seal.tmp"
    script = (
        f"{REMOTE_UV_DISCOVERY_SCRIPT}; "
        f"expected_seal=$(cat {staging_seal.shell_quote()}); "
        '[ -n "$expected_seal" ] || exit 73; '
        f"rm -f -- {staging_seal.shell_quote()}; "
        f"mkdir -p {final_root.parent.shell_quote()} {metadata_root.shell_quote()}; "
        f"if [ -d {final_root.shell_quote()} ]; then "
        f"existing_manifest=$(sha256sum {final_manifest.shell_quote()} 2>/dev/null | cut -d' ' -f1 || true); "
        f'[ "$existing_manifest" = {shlex.quote(manifest_digest)} ] || '
        '{ echo "deployment ID content mismatch" >&2; exit 73; }; '
        f'actual_seal=$("$remote_uv" run --no-project --python {shlex.quote(python_version)} '
        f"python -c {shlex.quote(_DEPLOYMENT_SEAL_SCRIPT)} {final_root.shell_quote()}); "
        '[ "$actual_seal" = "$expected_seal" ] || '
        '{ echo "deployment ID content mismatch" >&2; exit 73; }; '
        f"rm -rf -- {staging_root.shell_quote()}; "
        f"else mv -T {staging_root.shell_quote()} {final_root.shell_quote()}; fi; "
        f'actual_seal=$("$remote_uv" run --no-project --python {shlex.quote(python_version)} '
        f"python -c {shlex.quote(_DEPLOYMENT_SEAL_SCRIPT)} {final_root.shell_quote()}); "
        '[ "$actual_seal" = "$expected_seal" ] || '
        '{ echo "deployment ID content mismatch" >&2; exit 73; }; '
        f"existing_expected=$(cat {expected_seal_path.shell_quote()} 2>/dev/null || true); "
        '[ -z "$existing_expected" ] || [ "$existing_expected" = "$expected_seal" ] || '
        '{ echo "deployment seal metadata mismatch" >&2; exit 73; }; '
        f"printf '%s\\n' \"$expected_seal\" > {seal_temp.shell_quote()}; "
        f"mv -T {seal_temp.shell_quote()} {expected_seal_path.shell_quote()}; "
        f"date +%s > {(metadata_root / 'last-used').shell_quote()}"
    )
    return _locked_command(lock_path, script)


_DEPLOYMENT_SEAL_SCRIPT = """
import hashlib
import os
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1])
digest = hashlib.sha256()
paths = [root, *root.rglob("*")]
for path in sorted(paths, key=lambda item: "." if item == root else item.relative_to(root).as_posix()):
    relative = "." if path == root else path.relative_to(root).as_posix()
    if relative == "deployment.seal":
        continue
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode):
        kind = b"L"
        payload = os.readlink(path).encode("utf-8")
    elif stat.S_ISREG(mode):
        kind = b"F"
        payload = path.read_bytes()
    elif stat.S_ISDIR(mode):
        kind = b"D"
        payload = b""
    else:
        raise SystemExit(f"unsupported deployment entry: {relative}")
    digest.update(relative.encode("utf-8"))
    digest.update(b"\\0")
    digest.update(kind)
    digest.update(b"\\0")
    digest.update(oct(stat.S_IMODE(mode)).encode("ascii"))
    digest.update(b"\\0")
    digest.update(str(len(payload)).encode("ascii"))
    digest.update(b"\\0")
    digest.update(hashlib.sha256(payload).hexdigest().encode("ascii"))
    digest.update(b"\\n")
print(digest.hexdigest())
""".strip()


__all__ = [
    "PreparedRemoteDeployment",
    "RemoteDeploymentError",
    "RemoteDeploymentManager",
]
