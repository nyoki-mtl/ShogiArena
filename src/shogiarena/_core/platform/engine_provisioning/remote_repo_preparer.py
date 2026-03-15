"""Remote repository preparation helpers."""

from __future__ import annotations

import logging
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol, cast

from shogiarena._core.platform.engine_provisioning.file_hashing import sha256_file
from shogiarena._core.platform.engine_provisioning.provisioning_ports import (
    InstanceConfigPort,
    SshCommandTransportPort,
)
from shogiarena._core.platform.engine_provisioning.remote_execution_config import RemoteSyncMode


class _RemoteExecutionConfigPort(Protocol):
    github_token: str | None
    override_ref: str | None
    sync_mode: RemoteSyncMode


CopyDirectorySshFn = Callable[..., Awaitable[None]]

RemoteRepoProvisionerFactory = Callable[..., Any]


class RemoteRepoPreparer:
    """Handle remote repository preparation and optional overlay sync."""

    def __init__(
        self,
        *,
        instance: Any,
        repo: Any,
        transport: SshCommandTransportPort,
        config: _RemoteExecutionConfigPort,
        path_resolver: Any,
        project_locator: Any,
        repo_provisioner_factory: RemoteRepoProvisionerFactory,
        copy_directory: CopyDirectorySshFn | None = None,
        logger: logging.Logger,
    ) -> None:
        self._instance = instance
        self._repo = repo
        self._transport = transport
        self._config = config
        self._path_resolver = path_resolver
        self._project_locator = project_locator
        self._repo_provisioner_factory = repo_provisioner_factory
        self._copy_directory = copy_directory
        self._logger = logger
        self._uv_lock_path = self._project_locator.root / "uv.lock"
        if not self._uv_lock_path.exists():
            raise RuntimeError("uv.lock not found in workspace; run `make sync` first")
        self._lock_signature = sha256_file(self._uv_lock_path)
        self._is_requirements_checked = False

    @property
    def _ref_signature(self) -> str:
        ref = self._config.override_ref or self._repo.ref
        return ref[:12]

    async def ensure_repo(self, *, remote_root: str, absolute_root: str) -> None:
        spec = SimpleNamespace(base=remote_root, url=self._repo.url, ref=self._repo.ref)
        provisioner = self._repo_provisioner_factory(self._transport, spec)
        await self._ensure_requirements()
        repo_exists = await self._repo_exists(absolute_root)
        await provisioner.ensure_repo(
            base_path=absolute_root,
            token=self._config.github_token,
            override_ref=self._config.override_ref,
        )
        await provisioner.ensure_uv(
            base_path=absolute_root,
            local_lock_path=self._uv_lock_path,
            lock_hash=self._lock_signature,
        )
        operation = "clone" if not repo_exists else "update"
        self._logger.info(
            "[%s] repo %s complete (ref=%s)",
            self._instance.name,
            operation,
            self._ref_signature,
        )

    async def ensure_overlay(self, absolute_root: str) -> None:
        if self._config.sync_mode is not RemoteSyncMode.OVERLAY:
            return
        marker_path = f"{absolute_root}/.arena/overlay.once"
        if await self._path_resolver.file_exists(marker_path):
            return
        await self._overlay_directories(absolute_root)
        await self._write_overlay_markers(absolute_root)
        self._logger.info("[%s] overlay sync complete", self._instance.name)

    async def _ensure_requirements(self) -> None:
        if self._is_requirements_checked:
            return
        self._is_requirements_checked = True
        config = cast(InstanceConfigPort, self._instance.config)
        if not config.should_install_requirements:
            return

        instance_name = self._instance.name
        self._logger.debug("[%s] checking remote requirements", instance_name)

        async def _command_exists(cmd: str) -> bool:
            rc, _out, _err = await self._transport.run(f"command -v {cmd}")
            return rc == 0

        apt_packages: set[str] = set()
        command_to_package = [
            ("git", "git"),
            ("curl", "curl"),
            ("mpstat", "sysstat"),
        ]
        for command, package in command_to_package:
            if not await _command_exists(command):
                apt_packages.add(package)

        if apt_packages:
            if await _command_exists("apt-get"):
                pkg_list = " ".join(sorted(apt_packages))
                install_cmd = f"sudo -n apt-get update && sudo -n apt-get install -y {pkg_list}"
                rc, _out, err = await self._transport.run(install_cmd)
                if rc != 0:
                    install_cmd = f"apt-get update && apt-get install -y {pkg_list}"
                    rc, _out, err = await self._transport.run(install_cmd)
                if rc == 0:
                    self._logger.info("[%s] installed packages: %s", instance_name, pkg_list)
                else:
                    self._logger.warning(
                        "[%s] failed to install packages (%s); install manually: %s",
                        instance_name,
                        pkg_list,
                        err.strip() if isinstance(err, str) else err,
                    )
            else:
                self._logger.warning(
                    "[%s] apt-get not available; install required packages manually: %s",
                    instance_name,
                    ", ".join(sorted(apt_packages)),
                )

        if not await _command_exists("uv"):
            self._logger.debug("[%s] installing uv via official script", instance_name)
            install_uv_cmd = "curl -LsSf https://astral.sh/uv/install.sh | sh"
            rc, _out, err = await self._transport.run(install_uv_cmd)
            if rc != 0:
                self._logger.warning(
                    "[%s] failed to install uv automatically; install manually: %s",
                    instance_name,
                    err.strip() if isinstance(err, str) else err,
                )
            else:
                self._logger.debug("[%s] uv installation completed", instance_name)

    async def _repo_exists(self, absolute_root: str) -> bool:
        command = f"test -d {shlex.quote(absolute_root)}/.git"
        rc, _, _ = await self._transport.run(command)
        return rc == 0

    async def _overlay_directories(self, absolute_root: str) -> None:
        copy_directory = self._copy_directory or _default_copy_directory_scp
        await copy_directory(
            self._instance,
            self._project_locator.src_dir,
            f"{absolute_root}/src/shogiarena",
        )
        await copy_directory(
            self._instance,
            self._project_locator.configs_dir,
            f"{absolute_root}/configs",
        )

    async def _write_overlay_markers(self, absolute_root: str) -> None:
        arena_dir = f"{absolute_root}/.arena"
        rc, _, stderr = await self._transport.run("mkdir -p " + shlex.quote(arena_dir))
        if rc != 0:
            raise RuntimeError(f"Failed to create overlay marker directory: {stderr}")
        payload = f"{self._ref_signature}:{self._lock_signature}"
        marker_path = f"{arena_dir}/overlay.once"
        rc_marker, _, marker_err = await self._transport.run(
            "printf %s " + shlex.quote(payload) + " > " + shlex.quote(marker_path)
        )
        if rc_marker != 0:
            raise RuntimeError(f"Failed to write overlay marker: {marker_err}")
        timestamp_cmd = "date +%s > " + shlex.quote(f"{arena_dir}/overlay.active")
        rc_active, _, active_err = await self._transport.run(timestamp_cmd)
        if rc_active != 0:
            raise RuntimeError(f"Failed to mark overlay as active: {active_err}")


async def _default_copy_directory_scp(instance: Any, local_dir: Path, remote_dir: str) -> None:
    from shogiarena._core.contexts.instances.ports.engine_runtime_port import copy_directory_scp

    await copy_directory_scp(instance=instance, local_dir=local_dir, remote_dir=remote_dir)


__all__ = ["RemoteRepoPreparer"]
