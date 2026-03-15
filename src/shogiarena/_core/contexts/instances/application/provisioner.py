"""Provision helper for copying engine directories to SSH instances via asyncssh + tar.

This implementation does not rely on system ssh/scp binaries. All transport and
process execution on the remote host is performed through asyncssh.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import posixpath
import shlex
from collections.abc import Iterator, Mapping
from pathlib import Path

import asyncssh

from shogiarena._core.contexts.instances.application.ssh_transport import create_transport
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize

from .instance_models import Instance, InstanceConfig, InstanceType

logger = logging.getLogger(__name__)


class ProvisionError(RuntimeError):
    pass


class Provisioner:
    # Per-remote-binary path locks to avoid duplicated concurrent uploads within a process
    _binary_locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    async def _resolve_remote_path(instance: Instance, path_str: str) -> str:
        """Resolve $VAR and ~ on remote host; no-op for local.

        SFTP does not expand shell variables, so resolve before mkdir/put.
        """
        if instance.config.type != InstanceType.SSH:
            return path_str
        t = create_transport(instance)
        await t.connect()
        try:
            cmd = "p=$(eval echo " + shlex.quote(path_str) + '); printf %s "$p"'
            rc, out, err = await t.run(cmd)
            if rc != 0 or not out:
                raise ProvisionError(f"Failed to resolve remote path: {path_str}: {err or out}")
            return out
        finally:
            try:
                await t.close()
            except (asyncssh.Error, OSError) as exc:
                logger.debug("Failed to close SSH transport after path resolve: %s", exc)

    @staticmethod
    async def copy_directory_scp(instance: Instance, local_dir: Path, remote_dir: str) -> None:
        """Copy local_dir to remote_dir using asyncssh (tar stream + atomic swap)."""
        if instance.config.type != InstanceType.SSH:
            raise ProvisionError("copy_directory_scp requires SSH instance")
        instance_config = instance.config
        if not local_dir.exists() or not local_dir.is_dir():
            raise ProvisionError(f"Local directory not found: {local_dir}")

        # Resolve env/user tokens on remote for target dir
        remote_dir = await Provisioner._resolve_remote_path(instance, remote_dir)

        remote_parent = posixpath.dirname(remote_dir.rstrip("/")) or "/"
        base = posixpath.basename(remote_dir.rstrip("/"))
        remote_tmp = posixpath.join(remote_parent, f".{base}.tmp")

        # Use transport for remote commands
        t = create_transport(instance)
        await t.connect()
        try:
            # Prepare remote tmp dir
            pre = f"set -e; rm -rf {shlex.quote(remote_tmp)}; mkdir -p {shlex.quote(remote_tmp)}"
            rc, _out, err = await t.run(f"bash -lc {shlex.quote(pre)}")
            if rc != 0:
                raise ProvisionError(f"Failed to prepare remote tmp dir: {err.strip()}")

            # Stream tar to remote tmp via asyncssh process
            await Provisioner._tar_stream_to_remote(instance_config, local_dir, remote_tmp)

            # Verify tmp has content before swapping (avoid replacing with empty dir)
            ver = (
                f"set -e; test -d {shlex.quote(remote_tmp)}; "
                f"cnt=$(find {shlex.quote(remote_tmp)} -type f -print -quit | wc -l); "
                'if [ "$cnt" = "0" ]; then echo \'provision: empty extract\' 1>&2; exit 97; fi'
            )
            rc, _out, err = await t.run(f"bash -lc {shlex.quote(ver)}")
            if rc != 0:
                raise ProvisionError(f"Remote tmp verify failed: {err.strip()}")

            # Atomic replace
            swap = (
                f"set -e; mkdir -p {shlex.quote(remote_parent)}; "
                f"rm -rf {shlex.quote(remote_dir)}; mv {shlex.quote(remote_tmp)} {shlex.quote(remote_dir)}"
            )
            rc, _out, err = await t.run(f"bash -lc {shlex.quote(swap)}")
            if rc != 0:
                raise ProvisionError(f"Atomic replace failed: {err.strip()}")
            logger.info("[%s] synced directory: %s", instance.name, remote_dir)
        finally:
            try:
                await t.close()
            except (asyncssh.Error, OSError) as exc:
                logger.debug("Failed to close SSH transport after directory copy: %s", exc)

    @staticmethod
    async def ensure_remote_binary(instance: Instance, local_binary: Path, remote_binary: str) -> None:
        """Ensure a single engine binary exists on remote; skip upload when unchanged.

        Optimization:
        - Uses a sidecar marker at ``<remote_binary>.sha256`` to cache the hash of the
          last uploaded binary. When the local file hash matches and the remote file
          exists, the upload is skipped.
        - Falls back to full upload when marker missing or mismatch.
        """
        if instance.config.type != InstanceType.SSH:
            return
        if not local_binary.exists() or not local_binary.is_file():
            raise ProvisionError(f"Local binary not found: {local_binary}")

        # Coarse lock per provided remote path string to avoid duplicate uploads
        lock = Provisioner._binary_locks.setdefault(remote_binary, asyncio.Lock())
        async with lock:
            # Compute local hash once
            local_hash = Provisioner._sha256_file(local_binary)

            # Resolve remote paths
            remote_binary_resolved = await Provisioner._resolve_remote_path(instance, remote_binary)
            marker_path = remote_binary_resolved + ".sha256"

            # Use a single transport here for both read and final write (ensure_remote_file opens its own)
            t = create_transport(instance)
            await t.connect()
            try:
                # Best-effort short-circuit if remote exists and marker matches
                cmd_chk = (
                    f"test -f {shlex.quote(remote_binary_resolved)} && "
                    f"cat {shlex.quote(marker_path)} 2>/dev/null || true"
                )
                rc_chk, out_chk, _ = await t.run(cmd_chk)
                marker = (out_chk or "").strip() if rc_chk == 0 else ""
                if marker and marker == local_hash:
                    logger.debug("[%s] remote binary up-to-date (cached): %s", instance.name, remote_binary_resolved)
                    return

                # Upload and set exec bit when not cached
                await Provisioner.ensure_remote_file(
                    instance,
                    local_binary,
                    remote_binary_resolved,
                    should_set_executable=True,
                )
                # Write/refresh marker
                await t.write_bytes(marker_path, (local_hash + "\n").encode())
            finally:
                try:
                    await t.close()
                except (asyncssh.Error, OSError) as exc:
                    logger.debug("Failed to close SSH transport after ensure_remote_binary: %s", exc)

    @staticmethod
    async def ensure_remote_file(
        instance: Instance, local_path: Path, remote_path: str, *, should_set_executable: bool = False
    ) -> None:
        """Copy a single file to the remote path atomically via SshTransport.

        Creates parent directory, uploads to a tmp file, then atomic mv into place.
        Optionally sets executable bit.
        """
        if instance.config.type != InstanceType.SSH:
            return
        if not local_path.exists() or not local_path.is_file():
            raise ProvisionError(f"Local file not found: {local_path}")

        # Resolve env/user tokens on remote
        remote_path = await Provisioner._resolve_remote_path(instance, remote_path)
        parent = posixpath.dirname(remote_path)
        staging_remote_path = remote_path + ".tmp"
        t = create_transport(instance)
        await t.connect()
        try:
            if parent:
                await t.mkdir(parent, is_existing_ok=True)
            await t.put_file(local_path, staging_remote_path)
            # Atomic replace and optional exec bit
            chmod = f"; chmod +x {shlex.quote(remote_path)}" if should_set_executable else ""
            rc, _out, err = await t.run(
                f"set -e; mv {shlex.quote(staging_remote_path)} {shlex.quote(remote_path)}{chmod}"
            )
            if rc != 0:
                raise ProvisionError(f"Failed to finalize upload to {remote_path}: {err.strip()}")
        finally:
            try:
                await t.close()
            except (asyncssh.Error, OSError) as exc:
                logger.debug("Failed to close SSH transport after file ensure: %s", exc)

    @staticmethod
    async def _tar_stream_to_remote(instance_config: InstanceConfig, local_dir: Path, remote_tmp: str) -> None:
        """Stream a local tar archive to remote and extract using asyncssh."""
        # local tar sender
        sender = await asyncio.create_subprocess_exec(
            "tar",
            "-C",
            str(local_dir),
            "-cf",
            "-",
            ".",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # remote receiver via asyncssh
        client_keys = [instance_config.identity_file] if instance_config.identity_file else None
        if not instance_config.is_strict_host_key_checking:
            conn = await asyncssh.connect(
                instance_config.host,
                port=instance_config.port,
                username=instance_config.user,
                client_keys=client_keys,
                known_hosts=None,
            )
        else:
            conn = await asyncssh.connect(
                instance_config.host,
                port=instance_config.port,
                username=instance_config.user,
                client_keys=client_keys,
            )
        try:
            # Use binary mode (encoding=None) so we can stream raw tar bytes
            proc = await conn.create_process(f"tar -xpf - -C {shlex.quote(remote_tmp)}", encoding=None)
            stdout = sender.stdout
            if stdout is None:
                raise ProvisionError("tar sender stdout missing; cannot stream to remote")
            try:
                while True:
                    chunk = await stdout.read(64 * 1024)
                    if not chunk:
                        break
                    proc.stdin.write(chunk)
                    await proc.stdin.drain()
            finally:
                try:
                    proc.stdin.close()
                except (asyncssh.Error, OSError, AttributeError) as exc:
                    logger.debug("Failed to close remote tar stdin: %s", exc)
            # wait for both
            _out_s, err_s = await sender.communicate()
            await proc.wait()
            if sender.returncode != 0:
                raise ProvisionError(f"tar create failed: {err_s.decode().strip()}")
            if int(proc.exit_status or 0) != 0:
                raise ProvisionError("remote tar extract failed")
        finally:
            conn.close()
            try:
                await conn.wait_closed()
            except (asyncssh.Error, OSError) as exc:
                logger.debug("Failed to await SSH connection close: %s", exc)

    # --- Manifest helpers -------------------------------------------------
    @staticmethod
    def _sha256_file(p: Path) -> str:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _iter_files(root: Path) -> Iterator[Path]:
        for p in root.rglob("*"):
            if p.is_file():
                yield p

    @staticmethod
    def build_local_manifest(local_dir: Path, meta: JsonObject | None = None) -> JsonObject:
        entries: list[JsonObject] = []
        for f in Provisioner._iter_files(local_dir):
            entries.append(
                {
                    "path": f.relative_to(local_dir).as_posix(),
                    "size": f.stat().st_size,
                    "sha256": Provisioner._sha256_file(f),
                }
            )
        manifest: JsonObject = {
            "version": 1,
            "meta": meta or {},
            "entries": sorted(entries, key=lambda entry: str(entry.get("path", ""))),
        }
        digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        manifest["digest"] = digest
        return manifest

    @staticmethod
    async def read_remote_manifest(instance: Instance, remote_dir: str) -> JsonObject | None:
        # Resolve remote_dir first
        remote_dir = await Provisioner._resolve_remote_path(instance, remote_dir)
        manifest_path = str(posixpath.join(remote_dir, ".manifest.json"))
        if instance.config.type != InstanceType.SSH:
            return None
        t = create_transport(instance)
        await t.connect()
        try:
            rc, out, _err = await t.run(f"cat {shlex.quote(manifest_path)} 2>/dev/null || true")
            if rc != 0:
                return None
            if not out or not out.strip():
                return None
            try:
                obj = json.loads(out)
                if not isinstance(obj, dict):
                    return None
                return {str(key): json_serialize(value) for key, value in obj.items()}
            except json.JSONDecodeError as e:
                logger.debug("Remote manifest is not valid JSON: %s", e)
                return None
        finally:
            try:
                await t.close()
            except (asyncssh.Error, OSError) as exc:
                logger.debug("Failed to close SSH transport after reading manifest: %s", exc)

    @staticmethod
    async def write_remote_manifest(instance: Instance, remote_dir: str, manifest: Mapping[str, JsonValue]) -> None:
        if instance.config.type != InstanceType.SSH:
            return
        remote_dir = await Provisioner._resolve_remote_path(instance, remote_dir)
        manifest_path = str(posixpath.join(remote_dir, ".manifest.json"))
        payload: JsonObject = {str(key): json_serialize(value) for key, value in manifest.items()}
        data = json.dumps(payload, sort_keys=True).encode()
        t = create_transport(instance)
        await t.connect()
        await t.mkdir(remote_dir, is_existing_ok=True)
        await t.write_bytes(manifest_path, data)

    @staticmethod
    async def ensure_remote_dir_by_manifest(instance: Instance, local_dir: Path, remote_dir: str) -> None:
        if instance.config.type != InstanceType.SSH:
            return
        # Resolve once for subsequent calls
        remote_dir_resolved = await Provisioner._resolve_remote_path(instance, remote_dir)
        local_manifest = Provisioner.build_local_manifest(local_dir)
        remote_manifest = await Provisioner.read_remote_manifest(instance, remote_dir_resolved)
        if remote_manifest and remote_manifest.get("digest") == local_manifest.get("digest"):
            return
        await Provisioner.copy_directory_scp(instance, local_dir, remote_dir_resolved)
        await Provisioner.write_remote_manifest(instance, remote_dir_resolved, local_manifest)

    # System ssh/scp helpers (_build_ssh_cmd/_run_cmd) are not used; asyncssh is used for transport
