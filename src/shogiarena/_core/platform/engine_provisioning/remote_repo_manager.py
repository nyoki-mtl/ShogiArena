"""Remote repository sync helpers for runtime orchestration."""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from shogiarena._core.platform.engine_provisioning.file_hashing import sha256_file
from shogiarena._core.platform.engine_provisioning.provisioning_ports import SshCommandTransportPort


@dataclass(frozen=True)
class RemoteRepoSpec:
    """Describe the remote repository checkout target."""

    base: str
    url: str
    ref: str


class RemoteRepoSynchronizer:
    """Prepare a remote Git repository and uv environment via SSH."""

    def __init__(self, transport: SshCommandTransportPort, spec: RemoteRepoSpec) -> None:
        self._transport = transport
        self._spec = spec
        self._logger = logging.getLogger(__name__)

    async def ensure_repo(
        self,
        *,
        base_path: str,
        token: str | None = None,
        override_ref: str | None = None,
    ) -> None:
        await self._ensure_parent_directory(base_path)
        await self._require_git()
        target_ref = override_ref or self._spec.ref
        self._logger.debug("[%s] ensuring git repository (ref=%s)", base_path, target_ref)

        if await self._is_git_repository(base_path):
            self._logger.debug("[%s] repository exists; fetching updates", base_path)
        else:
            self._logger.debug("[%s] cloning repository from %s", base_path, self._spec.url)
            await self._clone_repository(base_path, token)

        await self._fetch_updates(base_path, token)
        await self._checkout_ref(base_path, target_ref)
        await self._reset_ref(base_path, target_ref)

    async def ensure_uv(
        self,
        *,
        base_path: str,
        local_lock_path: Path,
        lock_hash: str | None = None,
    ) -> None:
        await self._require_uv()
        lock_hash_value = lock_hash or sha256_file(local_lock_path)
        arena_dir = PurePosixPath(base_path) / ".arena"
        await self._ensure_directory(str(arena_dir))

        marker_path = arena_dir / "uv.lock.sha256"
        current_hash = await self._read_remote_file(str(marker_path))
        if current_hash == lock_hash_value:
            self._logger.debug("[%s] uv sync skipped (lock hash unchanged)", base_path)
            return

        sync_cmd = (
            f'export PATH="$HOME/.local/bin:$PATH"; uv --directory {shlex.quote(base_path)} sync --all-extras --frozen'
        )
        self._logger.debug("[%s] running uv sync", base_path)
        rc, out, err = await self._transport.run(sync_cmd)
        if rc != 0:
            message = err or out or "uv sync failed"
            raise RuntimeError(f"uv sync failed on remote: {message}")

        write_cmd = f"printf %s {shlex.quote(lock_hash_value)} > {shlex.quote(str(marker_path))}"
        rc_write, _out_write, err_write = await self._transport.run(write_cmd)
        if rc_write != 0:
            raise RuntimeError(f"Failed to write uv lock marker on remote: {err_write}")

    async def _clone_repository(self, base_path: str, token: str | None) -> None:
        if await self._path_exists(base_path):
            raise RuntimeError(f"Remote path {base_path} exists but is not a Git repository; remove it manually.")
        clone_url = self._auth_url(token)
        parent_dir = str(PurePosixPath(base_path).parent)
        await self._ensure_directory(parent_dir)
        cmd = f"git clone {shlex.quote(clone_url)} {shlex.quote(base_path)}"
        rc, out, err = await self._transport.run(cmd)
        if rc != 0:
            message = err or out or "git clone failed"
            raise RuntimeError(f"git clone failed on remote: {message}")
        self._logger.debug("[%s] cloned repository", base_path)

    async def _fetch_updates(self, base_path: str, token: str | None) -> None:
        fetch_url = self._auth_url(token)
        cmd = (
            f"git -C {shlex.quote(base_path)} fetch --prune "
            f"{shlex.quote(fetch_url)} +refs/heads/*:refs/remotes/origin/*"
        )
        self._logger.debug("[%s] git fetch --prune", base_path)
        rc, out, err = await self._transport.run(cmd)
        if rc != 0:
            message = err or out or "git fetch failed"
            raise RuntimeError(f"git fetch failed on remote: {message}")

    async def _checkout_ref(self, base_path: str, ref: str) -> None:
        checkout_cmd = self._checkout_command(base_path, ref)
        self._logger.debug("[%s] git checkout %s", base_path, ref)
        rc, out, err = await self._transport.run(checkout_cmd)
        if rc != 0:
            message = err or out or "git checkout failed"
            raise RuntimeError(f"git checkout failed on remote: {message}")

    async def _reset_ref(self, base_path: str, ref: str) -> None:
        cmd = f"git -C {shlex.quote(base_path)} reset --hard {shlex.quote(ref)}"
        self._logger.debug("[%s] git reset --hard %s", base_path, ref)
        rc, out, err = await self._transport.run(cmd)
        if rc != 0:
            message = err or out or "git reset --hard failed"
            raise RuntimeError(f"git reset --hard failed on remote: {message}")

    async def _is_git_repository(self, base_path: str) -> bool:
        git_dir = PurePosixPath(base_path) / ".git"
        return await self._path_exists(str(git_dir))

    async def _path_exists(self, remote_path: str) -> bool:
        rc, _out, _err = await self._transport.run(f"test -e {shlex.quote(remote_path)}")
        return rc == 0

    async def _ensure_directory(self, remote_path: str) -> None:
        rc, _out, err = await self._transport.run(f"mkdir -p {shlex.quote(remote_path)}")
        if rc != 0:
            raise RuntimeError(f"Failed to create directory {remote_path} on remote: {err}")

    async def _ensure_parent_directory(self, base_path: str) -> None:
        parent = PurePosixPath(base_path).parent
        await self._ensure_directory(str(parent))

    def _auth_url(self, token: str | None) -> str:
        if token and self._spec.url.startswith("https://"):
            return self._spec.url.replace("https://", f"https://x-access-token:{token}@", 1)
        return self._spec.url

    async def _require_git(self) -> None:
        rc, _out, _err = await self._transport.run("command -v git")
        if rc != 0:
            raise RuntimeError("git is required on the remote host but not found in PATH.")

    async def _require_uv(self) -> None:
        rc, _out, _err = await self._transport.run('export PATH="$HOME/.local/bin:$PATH"; command -v uv')
        if rc != 0:
            raise RuntimeError("uv is required on the remote host but not found in PATH.")

    async def _read_remote_file(self, remote_path: str) -> str:
        rc, out, _err = await self._transport.run(f"cat {shlex.quote(remote_path)} 2>/dev/null")
        if rc != 0:
            return ""
        return out.strip()

    def _checkout_command(self, base_path: str, ref: str) -> str:
        is_commit = self._looks_like_commit(ref)
        quoted_ref = shlex.quote(ref)
        base_cmd = f"git -C {shlex.quote(base_path)} checkout -f"
        if is_commit:
            return f"{base_cmd} --detach {quoted_ref}"
        return f"{base_cmd} {quoted_ref}"

    @staticmethod
    def _looks_like_commit(ref: str) -> bool:
        if not (7 <= len(ref) <= 40):
            return False
        hex_chars = set("0123456789abcdefABCDEF")
        return all(char in hex_chars for char in ref)


__all__ = ["RemoteRepoSpec", "RemoteRepoSynchronizer"]
