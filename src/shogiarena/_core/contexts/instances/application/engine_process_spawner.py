"""Engine process spawner abstraction for local and SSH execution.

Notes:
- SSH execution uses asyncssh exclusively; there is no fallback to system OpenSSH.
- Host key verification follows instance settings (strict by default) via asyncssh.
"""

import asyncio
import logging
import shlex
from pathlib import Path
from typing import Any

import asyncssh

from .engine_process_handles import (
    EngineProcess,
    LocalEngineProcess,
    SSHEngineProcess,
    apply_cpu_affinity,
    wrap_with_taskset,
)
from .instance_models import Instance

logger = logging.getLogger(__name__)


class EngineProcessSpawner:
    """Factory for spawning engine processes on different instance types."""

    @staticmethod
    async def spawn(
        instance: Instance,
        engine_path: str,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
        engine_args: list[str] | None = None,
        cpu_affinity: tuple[int, ...] | None = None,
    ) -> EngineProcess:
        """
        Spawn an engine process on the specified instance.

        Args:
            instance: Instance to run the engine on
            engine_path: Path to engine executable (relative to engine_dir or absolute)
            working_dir: Working directory (defaults to engine executable's parent directory)
            env: Environment variables
            engine_args: Additional engine arguments

        Returns:
            EngineProcess wrapper for the spawned process

        Raises:
            RuntimeError: If process spawning fails
            ValueError: If instance configuration is invalid
        """
        if instance.is_local:
            return await EngineProcessSpawner._spawn_local(
                instance, engine_path, working_dir, env, engine_args, cpu_affinity
            )
        if instance.is_ssh:
            return await EngineProcessSpawner._spawn_ssh(
                instance, engine_path, working_dir, env, engine_args, cpu_affinity
            )
        raise ValueError(f"Unsupported instance type: {instance.type}")

    @staticmethod
    async def _spawn_local(
        instance: Instance,
        engine_path: str,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
        engine_args: list[str] | None = None,
        cpu_affinity: tuple[int, ...] | None = None,
    ) -> LocalEngineProcess:
        """Spawn local engine process."""
        if instance.config.engine_dir:
            engine_dir_path_raw = Path(instance.config.engine_dir)
            engine_dir_path = (
                engine_dir_path_raw
                if engine_dir_path_raw.is_absolute()
                else (Path.cwd() / engine_dir_path_raw).resolve()
            )
        else:
            engine_dir_path = Path.cwd().resolve()

        engine_path_obj = Path(engine_path)
        if engine_path_obj.is_absolute():
            resolved_engine_path = engine_path_obj
        else:
            resolved_engine_path = (engine_dir_path / engine_path_obj).resolve()
        work_dir_path = Path(working_dir) if working_dir else resolved_engine_path.parent
        if not work_dir_path.is_absolute():
            base_dir = engine_dir_path if instance.config.engine_dir else Path.cwd().resolve()
            work_dir = (base_dir / work_dir_path).resolve()
        else:
            work_dir = work_dir_path

        cmd = [str(resolved_engine_path)]
        if engine_args:
            cmd.extend(engine_args)

        logger.debug(f"Starting local engine: {' '.join(cmd)} in {work_dir}")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                env=env,
                # Raise the StreamReader buffer well above the 64KB default so long
                # `info ... pv ...` lines (deep search / multipv) do not overrun readline().
                limit=8 * 1024 * 1024,
            )

            logger.debug(f"Local engine started: pid={process.pid}")

            if cpu_affinity:
                apply_cpu_affinity(process.pid, cpu_affinity, logger)

            return LocalEngineProcess(process)

        except (OSError, ValueError) as exc:
            logger.error("Failed to start local engine %s: %s", resolved_engine_path, exc)
            raise RuntimeError(f"Failed to start local engine: {exc}") from exc

    @staticmethod
    async def _spawn_ssh(
        instance: Instance,
        engine_path: str,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
        engine_args: list[str] | None = None,
        cpu_affinity: tuple[int, ...] | None = None,
    ) -> SSHEngineProcess:
        """Spawn SSH engine process via asyncssh (no system ssh dependency)."""
        config = instance.config
        if not config.host or not config.user:
            raise ValueError("SSH instance must have host and user configured")

        def _is_remote_absolute(path_str: str) -> bool:
            return path_str.startswith(("/", "~", "$"))

        remote_parts: list[str] = []
        if config.engine_dir:
            remote_parts.append(f"ENGINE_DIR=$(eval echo {shlex.quote(config.engine_dir)})")

        if working_dir:
            remote_wd = Path(working_dir)
            if not remote_wd.is_absolute() and not _is_remote_absolute(working_dir) and config.engine_dir:
                remote_parts.append('cd "$ENGINE_DIR"/' + shlex.quote(remote_wd.as_posix()))
            else:
                remote_parts.append(f"cd {shlex.quote(remote_wd.as_posix())}")
        elif config.engine_dir:
            remote_parts.append('cd "$ENGINE_DIR"')

        if env:
            for key, value in env.items():
                remote_parts.append(f"export {key}={shlex.quote(value)}")

        if Path(engine_path).is_absolute() or _is_remote_absolute(engine_path):
            full_engine_expr = shlex.quote(engine_path)
        else:
            if config.engine_dir:
                full_engine_expr = '"$ENGINE_DIR"/' + shlex.quote(engine_path)
            else:
                full_engine_expr = shlex.quote(engine_path)

        engine_cmd_parts = [full_engine_expr]
        if engine_args:
            engine_cmd_parts.extend(shlex.quote(arg) for arg in engine_args)
        engine_exec = " ".join(engine_cmd_parts)
        remote_parts.append(wrap_with_taskset(engine_exec, cpu_affinity))
        remote_command = " && ".join(remote_parts)

        client_keys = [config.identity_file] if config.identity_file else None
        try:
            if not config.is_strict_host_key_checking:
                conn = await asyncssh.connect(
                    config.host,
                    port=config.port,
                    username=config.user,
                    client_keys=client_keys,
                    known_hosts=None,
                )
            else:
                conn = await asyncssh.connect(
                    config.host,
                    port=config.port,
                    username=config.user,
                    client_keys=client_keys,
                )

            async def _create_binary_process(command: str) -> asyncssh.SSHClientProcess[Any]:
                return await conn.create_process(command, encoding=None)

            try:
                uname_proc = await _create_binary_process("uname -s")
                if uname_proc.stdout is not None:
                    uname_raw = await uname_proc.stdout.read()
                else:
                    uname_raw = ""
                await uname_proc.wait()

                if uname_proc.exit_status != 0:
                    logger.warning(
                        "Failed to detect remote OS via uname on %s (exit=%s); assuming Linux compatibility",
                        config.host,
                        uname_proc.exit_status,
                    )
                else:
                    normalized = uname_raw.decode() if isinstance(uname_raw, bytes) else str(uname_raw)
                    normalized = normalized.strip()
                    if "linux" not in normalized.lower():
                        raise RuntimeError(
                            f"Remote instance '{config.host}' reports unsupported OS via uname: {normalized}. "
                            "Only Linux targets are supported."
                        )

                bash_cmd = "bash -lc " + shlex.quote(remote_command)
                proc = await _create_binary_process(bash_cmd)
                logger.debug("SSH engine started via asyncssh (affinity=%s)", cpu_affinity)
                return SSHEngineProcess(instance, conn, proc)
            except (asyncssh.Error, OSError, RuntimeError, ValueError, TypeError) as exc:
                conn.close()
                try:
                    await conn.wait_closed()
                except (asyncssh.Error, OSError) as close_exc:
                    logger.debug("Failed to await SSH connection close: %s", close_exc)
                logger.debug("SSH engine spawn failed: %s", exc, exc_info=True)
                raise
        except (asyncssh.Error, OSError, RuntimeError, ValueError) as exc:
            logger.error("Failed to start SSH engine on %s: %s", config.host, exc)
            raise RuntimeError(f"Failed to start SSH engine: {exc}") from exc


__all__ = ["EngineProcess", "EngineProcessSpawner"]
