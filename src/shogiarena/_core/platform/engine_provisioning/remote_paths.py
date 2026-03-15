"""Remote path and local workspace location helpers."""

from __future__ import annotations

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
        command = "p=$(eval echo " + shlex.quote(expression) + '); printf %s "$p"'
        rc, stdout, stderr = await self._transport.run(command)
        if rc != 0 or not stdout:
            raise RuntimeError(f"Failed to resolve remote path: {expression}: {stderr or stdout}")
        return stdout

    async def file_exists(self, remote_path: str) -> bool:
        command = f"test -f {shlex.quote(remote_path)}"
        rc, _, _ = await self._transport.run(command)
        return rc == 0


__all__ = ["RemotePathResolver", "RemoteProjectLocator"]
