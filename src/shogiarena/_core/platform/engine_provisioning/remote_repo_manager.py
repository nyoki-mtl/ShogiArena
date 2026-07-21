"""Remote repository sync helpers for runtime orchestration."""

from __future__ import annotations

import logging
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from shogiarena._core.platform.engine_provisioning.file_hashing import sha256_file
from shogiarena._core.platform.engine_provisioning.provisioning_ports import (
    RemoteSecretFileHandle,
    RemoteSecretFileRequest,
    SshCommandTransportPort,
    SshSecretFileTransportPort,
)

_GIT_ASKPASS_SCRIPT = """#!/bin/sh
case "$1" in
  *Username*) printf "%s\\n" "x-access-token" ;;
  *)
    token=$(cat "$SHOGIARENA_GIT_TOKEN_FILE") || exit 1
    printf "%s\\n" "$token"
    ;;
esac
"""


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
        parent_dir = str(PurePosixPath(base_path).parent)
        await self._ensure_directory(parent_dir)
        cmd = f"git clone {shlex.quote(self._spec.url)} {shlex.quote(base_path)}"
        rc, out, err = await self._run_git_command(cmd, token=token)
        if rc != 0:
            message = err or out or "git clone failed"
            raise RuntimeError(f"git clone failed on remote: {message}")
        rc_url, _out_url, err_url = await self._transport.run(
            f"git -C {shlex.quote(base_path)} remote set-url origin {shlex.quote(self._spec.url)}"
        )
        if rc_url != 0:
            raise RuntimeError(f"Failed to reset remote origin URL after clone: {err_url}")
        self._logger.debug("[%s] cloned repository", base_path)

    async def _fetch_updates(self, base_path: str, token: str | None) -> None:
        cmd = (
            f"{self._git_command(base_path=base_path)} fetch --prune "
            f"{shlex.quote(self._spec.url)} +refs/heads/*:refs/remotes/origin/*"
        )
        self._logger.debug("[%s] git fetch --prune", base_path)
        rc, out, err = await self._run_git_command(cmd, token=token)
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
        reset_ref = ref if self._looks_like_commit(ref) else f"refs/remotes/origin/{ref}"
        cmd = f"git -C {shlex.quote(base_path)} reset --hard {shlex.quote(reset_ref)}"
        self._logger.debug("[%s] git reset --hard %s", base_path, reset_ref)
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

    async def _run_git_command(self, command: str, *, token: str | None) -> tuple[int, str, str]:
        if not token or not self._should_use_github_token():
            return await self._transport.run(command)
        secret_transport = self._secret_transport()
        secret_files = [
            RemoteSecretFileRequest(file_id="github_token", payload=token.encode("utf-8"), mode=0o600),
            RemoteSecretFileRequest(file_id="git_askpass", payload=_GIT_ASKPASS_SCRIPT.encode("utf-8"), mode=0o700),
        ]

        def build_secret_command(handles: Mapping[str, RemoteSecretFileHandle]) -> str:
            token_path = handles["github_token"].remote_path
            askpass_path = handles["git_askpass"].remote_path
            return (
                f"GIT_ASKPASS={shlex.quote(askpass_path)} "
                "GIT_TERMINAL_PROMPT=0 "
                f"SHOGIARENA_GIT_TOKEN_FILE={shlex.quote(token_path)} "
                f"{command}"
            )

        return await secret_transport.run_with_secret_files(build_secret_command, secret_files)

    def _secret_transport(self) -> SshSecretFileTransportPort:
        if not isinstance(self._transport, SshSecretFileTransportPort):
            raise RuntimeError("SSH transport does not support secret file handoff.")
        return self._transport

    def _git_command(self, *, base_path: str | None = None) -> str:
        parts = ["git"]
        if base_path is not None:
            parts.extend(["-C", shlex.quote(base_path)])
        return " ".join(parts)

    def _should_use_github_token(self) -> bool:
        parsed = urlparse(self._spec.url)
        if parsed.scheme not in {"http", "https"}:
            return False
        host = parsed.hostname or ""
        return host == "github.com" or host.endswith(".github.com")

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
        quoted_remote_ref = shlex.quote(f"refs/remotes/origin/{ref}")
        return f"git -C {shlex.quote(base_path)} checkout -B {quoted_ref} {quoted_remote_ref}"

    @staticmethod
    def _looks_like_commit(ref: str) -> bool:
        if not (7 <= len(ref) <= 40):
            return False
        hex_chars = set("0123456789abcdefABCDEF")
        return all(char in hex_chars for char in ref)


__all__ = ["RemoteRepoSpec", "RemoteRepoSynchronizer"]
