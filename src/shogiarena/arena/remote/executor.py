from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast

import yaml
from omegaconf import OmegaConf

from shogiarena.arena.instances.models import Instance, InstanceType
from shogiarena.arena.instances.provision import Provisioner
from shogiarena.arena.instances.remote_probe import detect_remote_target_cpu
from shogiarena.arena.remote.repo_manager import RemoteRepoManager, RemoteRepoSpec, hash_file
from shogiarena.arena.remote.ssh_transport import SshTransport, create_transport
from shogiarena.arena.services.artifacts.resolver import ArtifactResolver


class RemoteSyncMode(Enum):
    GIT = "git"
    OVERLAY = "overlay"

    @classmethod
    def from_env(cls, raw: str | None) -> RemoteSyncMode:
        if raw is None:
            return cls.GIT
        normalized = raw.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"Unsupported ARENA_REMOTE_SYNC_MODE: {raw}")


@dataclass(frozen=True)
class RemoteExecutionConfig:
    github_token: str | None
    export_github_token: bool
    override_ref: str | None
    sync_mode: RemoteSyncMode

    @classmethod
    def from_env(cls) -> RemoteExecutionConfig:
        token = os.environ.get("ARENA_REMOTE_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or None
        export_flag = os.environ.get("ARENA_REMOTE_EXPORT_GITHUB_TOKEN", "").strip().lower()
        export_github_token = export_flag in {"1", "true", "yes", "on"}
        override_ref = os.environ.get("ARENA_REMOTE_REF") or None
        sync_mode = RemoteSyncMode.from_env(os.environ.get("ARENA_REMOTE_SYNC_MODE"))
        return cls(
            github_token=token,
            export_github_token=export_github_token,
            override_ref=override_ref,
            sync_mode=sync_mode,
        )


class RemoteProjectLocator:
    """Locate project paths required for remote execution support."""

    def __init__(self) -> None:
        self._root = self._discover_root()
        self._src_dir = self._require_directory(self._root / "src" / "shogiarena")
        self._configs_dir = self._require_directory(self._root / "configs")

    @staticmethod
    def _discover_root() -> Path:
        here = Path(__file__).resolve()
        for candidate in [here] + list(here.parents):
            directory = candidate if candidate.is_dir() else candidate.parent
            if (directory / "pyproject.toml").exists() and (directory / "src").exists():
                return directory
        raise RuntimeError("Unable to locate project root containing pyproject.toml and src/")

    @staticmethod
    def _require_path(path: Path) -> Path:
        if not path.exists():
            raise RuntimeError(f"Expected path does not exist: {path}")
        return path

    @staticmethod
    def _require_directory(path: Path) -> Path:
        if not path.is_dir():
            raise RuntimeError(f"Expected directory does not exist: {path}")
        return path

    @property
    def root(self) -> Path:
        return self._root

    @property
    def src_dir(self) -> Path:
        return self._src_dir

    @property
    def configs_dir(self) -> Path:
        return self._configs_dir


class RemotePathResolver:
    """Perform path-related helpers against the remote shell."""

    def __init__(self, transport: SshTransport) -> None:
        self._transport = transport

    async def expand(self, expression: str) -> str:
        if "$" not in expression and not expression.startswith("~"):
            return expression
        command = "p=$(eval echo " + shlex.quote(expression) + '); printf %s "$p"'
        rc, stdout, stderr = await self._transport.run(command)
        if rc != 0 or not stdout:
            raise RuntimeError(f"Failed to resolve remote path: {expression}: {stderr or stdout}")
        return stdout

    async def file_exists(self, remote_path: str) -> bool:
        command = f"test -f {shlex.quote(remote_path)}"
        rc, _, _ = await self._transport.run(command)
        return rc == 0


class RemoteRepoPreparer:
    """Handle remote repository preparation and optional overlay sync."""

    def __init__(
        self,
        *,
        instance: Instance,
        repo: RemoteRepoSpec,
        transport: SshTransport,
        config: RemoteExecutionConfig,
        path_resolver: RemotePathResolver,
        project_locator: RemoteProjectLocator,
        logger: logging.Logger,
    ) -> None:
        self._instance = instance
        self._repo = repo
        self._transport = transport
        self._config = config
        self._path_resolver = path_resolver
        self._project_locator = project_locator
        self._logger = logger
        self._uv_lock_path = self._project_locator.root / "uv.lock"
        if not self._uv_lock_path.exists():
            raise RuntimeError("uv.lock not found in workspace; run `make sync` first")
        self._lock_signature = hash_file(self._uv_lock_path)
        self._requirements_checked = False

    @property
    def ref_signature(self) -> str:
        ref = self._config.override_ref or self._repo.ref
        return ref[:12]

    @property
    def lock_signature(self) -> str:
        return self._lock_signature

    async def ensure_repo(self, *, remote_root: str, absolute_root: str) -> None:
        spec = RemoteRepoSpec(base=remote_root, url=self._repo.url, ref=self._repo.ref)
        manager = RemoteRepoManager(self._transport, spec)
        await self._ensure_requirements()
        repo_exists = await self._repo_exists(absolute_root)
        await manager.ensure_repo(
            base_path=absolute_root,
            token=self._config.github_token,
            override_ref=self._config.override_ref,
        )
        await manager.ensure_uv(
            base_path=absolute_root,
            local_lock_path=self._uv_lock_path,
            lock_hash=self._lock_signature,
        )
        operation = "clone" if not repo_exists else "update"
        self._logger.info(
            "[%s] repo %s complete (ref=%s)",
            self._instance.name,
            operation,
            self.ref_signature,
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
        if self._requirements_checked:
            return
        self._requirements_checked = True
        config = getattr(self._instance, "config", None)
        if config is None or not getattr(config, "install_requirements", False):
            return

        instance_name = self._instance.name
        self._logger.debug("[%s] checking remote requirements", instance_name)

        async def _command_exists(cmd: str) -> bool:
            rc, _out, _err = await self._transport.run(f"command -v {cmd}")
            return rc == 0

        # Gather packages that can be satisfied via apt-get
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
                    # Retry without sudo (useful for non-sudo environments)
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

        # Ensure uv via official installer if missing
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
        await Provisioner.copy_directory_scp(
            self._instance,
            self._project_locator.src_dir,
            f"{absolute_root}/src/shogiarena",
        )
        await Provisioner.copy_directory_scp(
            self._instance,
            self._project_locator.configs_dir,
            f"{absolute_root}/configs",
        )

    async def _write_overlay_markers(self, absolute_root: str) -> None:
        arena_dir = f"{absolute_root}/.arena"
        rc, _, stderr = await self._transport.run("mkdir -p " + shlex.quote(arena_dir))
        if rc != 0:
            raise RuntimeError(f"Failed to create overlay marker directory: {stderr}")
        payload = f"{self.ref_signature}:{self._lock_signature}"
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


class RemoteStreamConsumer:
    """Consume JSON line streams from the remote runner."""

    def __init__(self, instance: Instance, transport: SshTransport, logger: logging.Logger) -> None:
        self._instance = instance
        self._transport = transport
        self._logger = logger

    async def collect(
        self,
        command: str,
        *,
        timeout: float | None,
        on_event: Callable[[dict[str, Any]], None] | None,
    ) -> list[dict[str, Any]]:
        line_iter = self._transport.run_stream_lines(command)
        events: list[dict[str, Any]] = []
        exit_code: int | None = None
        last_error: str | None = None
        last_text: str | None = None

        async def drain() -> None:
            nonlocal exit_code, last_error, last_text
            async for raw_line in line_iter:
                line = raw_line.strip()
                if line.startswith("__REMOTE_EXIT_RC:"):
                    try:
                        exit_code = int(line.split(":", 1)[1])
                    except ValueError as exc:
                        raise RuntimeError("Malformed exit code sentinel from remote runner") from exc
                    continue
                if line.startswith("{"):
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError as exc:
                        context = line[:200]
                        message = (
                            f"Malformed JSON from remote {self._instance.name}: {exc.msg} "
                            f"(pos {exc.pos}); context={context!r}"
                        )
                        self._logger.error(message)
                        raise RuntimeError(message) from exc
                    if isinstance(payload, dict):
                        events.append(payload)
                        if payload.get("type") == "error" and isinstance(payload.get("message"), str):
                            last_error = payload["message"][:500]
                        if on_event is not None:
                            on_event(payload)
                    continue
                if line:
                    last_text = line[:500]

        try:
            if timeout is None:
                await drain()
            else:
                await asyncio.wait_for(drain(), timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("remote pair timed out") from exc
        finally:
            await self._close_iterator(line_iter)

        if exit_code is None:
            raise RuntimeError("remote pair terminated without exit code sentinel")
        if exit_code != 0:
            message = f"remote pair failed with exit code {exit_code}"
            if last_error:
                message += f": {last_error}"
            elif last_text:
                message += f": {last_text}"
            raise RuntimeError(message)
        return events

    @staticmethod
    async def _close_iterator(iterator: AsyncIterator[str]) -> None:
        closer = getattr(iterator, "aclose", None)
        if closer is None:
            return
        await cast(Callable[[], Awaitable[None]], closer)()


def build_remote_runner_command(
    base_abs: str, remote_spec: str, spec_json: str, *, github_token: str | None = None
) -> str:
    """Construct the shell command responsible for launching the remote runner."""
    script_lines = [
        "set -e",
        'export PATH="$HOME/.local/bin:$PATH"',
        f"BASE={shlex.quote(base_abs)}",
        f"SPEC={shlex.quote(remote_spec)}",
        'mkdir -p "$BASE/.tmp"',
        "cat > \"$SPEC\" <<'__ARENA_SPEC_JSON__'",
        spec_json,
        "__ARENA_SPEC_JSON__",
    ]

    if github_token:
        script_lines.append(f"export GITHUB_TOKEN={shlex.quote(github_token)}")

    script_lines.extend(
        [
            'uv --directory "$BASE" run -- shogiarena _internal remote-run-pair --spec-file "$SPEC" 2>&1; rc=$?',
            "echo __REMOTE_EXIT_RC:$rc",
        ]
    )
    script = "\n".join(script_lines)
    return "bash -lc " + shlex.quote(script)


class RemoteExecutor:
    """Run a single game on an SSH instance using a cloned repo and uv."""

    _prepared_roots: set[str] = set()
    _prepare_tasks: dict[str, asyncio.Future[None]] = {}

    def __init__(self, instance: Instance, repo: RemoteRepoSpec) -> None:
        if instance.config.type != InstanceType.SSH:
            raise ValueError("RemoteExecutor requires SSH instance")
        self.instance = instance
        self.repo = repo
        self._logger = logging.getLogger(__name__)
        self._config = RemoteExecutionConfig.from_env()
        self._project_locator = RemoteProjectLocator()
        self._transport: SshTransport | None = None
        self._path_resolver: RemotePathResolver | None = None
        self._repo_preparer: RemoteRepoPreparer | None = None
        self._stream_consumer: RemoteStreamConsumer | None = None
        self._provisioned_bins: set[str] = set()

    @property
    def transport(self) -> SshTransport:
        if self._transport is None:
            self._transport = create_transport(self.instance)
            self._path_resolver = RemotePathResolver(self._transport)
            self._repo_preparer = RemoteRepoPreparer(
                instance=self.instance,
                repo=self.repo,
                transport=self._transport,
                config=self._config,
                path_resolver=self._path_resolver,
                project_locator=self._project_locator,
                logger=self._logger,
            )
            self._stream_consumer = RemoteStreamConsumer(self.instance, self._transport, self._logger)
        return self._transport

    @property
    def path_resolver(self) -> RemotePathResolver:
        if self._path_resolver is None:
            raise RuntimeError("Transport not initialized")
        return self._path_resolver

    @property
    def repo_preparer(self) -> RemoteRepoPreparer:
        if self._repo_preparer is None:
            raise RuntimeError("Transport not initialized")
        return self._repo_preparer

    @property
    def stream_consumer(self) -> RemoteStreamConsumer:
        if self._stream_consumer is None:
            raise RuntimeError("Transport not initialized")
        return self._stream_consumer

    async def _ensure_connected(self) -> None:
        await self.transport.connect()

    async def ensure_repo(self, remote_root: str) -> None:
        """Ensure the remote repository (plus optional overlay) is ready once per root."""
        await self._ensure_connected()
        key = self._cache_key(remote_root)
        if key in RemoteExecutor._prepared_roots:
            return

        inflight = RemoteExecutor._prepare_tasks.get(key)
        if inflight is not None:
            await inflight
            return

        loop = asyncio.get_running_loop()
        inflight = loop.create_future()
        RemoteExecutor._prepare_tasks[key] = inflight
        try:
            absolute_root = await self.path_resolver.expand(remote_root)
            await self.repo_preparer.ensure_repo(remote_root=remote_root, absolute_root=absolute_root)
            await self.repo_preparer.ensure_overlay(absolute_root)
            RemoteExecutor._prepared_roots.add(key)
            if not inflight.done():
                inflight.set_result(None)
        except (OSError, RuntimeError, ValueError) as exc:
            if not inflight.done():
                inflight.set_exception(exc)
            raise
        finally:
            RemoteExecutor._prepare_tasks.pop(key, None)

    async def provision_engine_binary(self, local_bin: Path) -> str:
        """Upload the engine binary if needed and return its remote path."""
        remote_dir = Path(self.instance.config.engine_dir) / local_bin.parent.name
        remote_bin = str(remote_dir / local_bin.name)
        if remote_bin in self._provisioned_bins:
            self._logger.debug("[%s] engine ready: %s", self.instance.name, remote_bin)
            return remote_bin
        await Provisioner.ensure_remote_binary(self.instance, local_bin, remote_bin)
        self._provisioned_bins.add(remote_bin)
        self._logger.debug("[%s] engine ready: %s", self.instance.name, remote_bin)
        return remote_bin

    async def resolve_local_binary(self, engine_config_path: Path) -> Path:
        """Resolve a local engine binary path from configuration or artifacts."""
        config = yaml.safe_load(engine_config_path.read_text(encoding="utf-8")) or {}
        engine_path = config.get("engine_path")
        if isinstance(engine_path, str) and engine_path.strip():
            return Path(engine_path)
        artifact = config.get("artifact")
        if not isinstance(artifact, str) or not artifact.strip():
            raise ValueError("engine config must specify engine_path or artifact")
        overrides = self._normalize_build_overrides(config.get("build_options"))
        target_cpu = str(overrides.get("target_cpu", "")).strip()
        if not target_cpu or target_cpu.lower() == "auto":
            overrides["target_cpu"] = await detect_remote_target_cpu(self.instance)
        resolver = ArtifactResolver(overwrite=False)
        return resolver.resolve(artifact, overrides=overrides)

    def _normalize_build_overrides(self, raw: Any) -> dict[str, Any]:
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return {str(k): v for k, v in raw.items()}
        container = OmegaConf.to_container(raw, resolve=True)
        if isinstance(container, dict):
            return {str(k): v for k, v in container.items()}
        raise TypeError("build_options must be a mapping")

    async def run_remote_pair(
        self,
        *,
        remote_root: str,
        spec: dict[str, Any],
        timeout: float | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a remote pair run and return the collected JSON events."""
        await self._ensure_connected()
        base_abs = await self.path_resolver.expand(remote_root)
        remote_spec = f"{base_abs}/.tmp/spec.json"
        spec_json = json.dumps(spec, ensure_ascii=False)
        command = build_remote_runner_command(
            base_abs,
            remote_spec,
            spec_json,
            github_token=self._config.github_token if self._config.export_github_token else None,
        )
        return await self.stream_consumer.collect(command, timeout=timeout, on_event=on_event)

    def _cache_key(self, remote_root: str) -> str:
        return f"{self.instance.name}::{remote_root}"

    def cache_signature(self) -> str:
        """Return a cache signature for repo sync decisions."""
        override_ref = self._config.override_ref or ""
        sync_mode = self._config.sync_mode.value
        return f"{self.repo.url}@{self.repo.ref}:{override_ref}:{sync_mode}"
