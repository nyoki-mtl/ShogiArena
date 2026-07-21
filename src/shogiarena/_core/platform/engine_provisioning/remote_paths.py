"""Remote path and local workspace location helpers."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from shogiarena._core.platform.engine_provisioning.provisioning_ports import SshCommandTransportPort


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

    def __init__(self, transport: SshCommandTransportPort) -> None:
        self._transport = transport

    async def expand(self, expression: str) -> str:
        if "$" not in expression and not expression.startswith("~"):
            return expression
        rc, stdout, stderr = await self._transport.run('printf "__HOME__=%s\\n" "$HOME"; env')
        if rc != 0 or not stdout:
            raise RuntimeError(f"Failed to resolve remote path: {expression}: {stderr or stdout}")
        return _expand_shell_path(expression, _parse_env(stdout))

    async def file_exists(self, remote_path: str) -> bool:
        command = f"test -f {shlex.quote(remote_path)}"
        rc, _, _ = await self._transport.run(command)
        return rc == 0


_ENV_VAR_PATTERN = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def _parse_env(raw: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in raw.splitlines():
        key, separator, value = line.partition("=")
        if separator and key:
            env[key] = value
    return env


def _expand_shell_path(expression: str, env: dict[str, str]) -> str:
    expanded = expression
    home = env.get("__HOME__") or env.get("HOME") or ""
    if expanded == "~":
        expanded = home
    elif expanded.startswith("~/"):
        expanded = f"{home}/{expanded[2:]}"

    def replace_var(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return env.get(name, "")

    return _ENV_VAR_PATTERN.sub(replace_var, expanded)


__all__ = ["RemotePathResolver", "RemoteProjectLocator"]
