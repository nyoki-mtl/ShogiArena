"""Remote path and local workspace location helpers."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from shogiarena._core.platform.engine_provisioning.provisioning_ports import SshCommandTransportPort


@dataclass(frozen=True, slots=True)
class RemotePosixPath:
    """Linux remote host上のpathを表すvalue object。"""

    _value: PurePosixPath

    def __init__(self, value: str | PurePosixPath) -> None:
        raw = str(value)
        if not raw:
            raise ValueError("remote path must not be empty")
        if "\\" in raw:
            raise ValueError(f"remote path must use POSIX separators: {raw!r}")
        if "\x00" in raw or "\n" in raw or "\r" in raw:
            raise ValueError("remote path contains an unsupported control character")
        object.__setattr__(self, "_value", PurePosixPath(raw))

    def __str__(self) -> str:
        return self._value.as_posix()

    def __truediv__(self, child: str) -> RemotePosixPath:
        if not child or "/" in child or "\\" in child or child in {".", ".."}:
            raise ValueError(f"remote path child must be one safe component: {child!r}")
        return RemotePosixPath(self._value / child)

    def join_relative(self, relative: str) -> RemotePosixPath:
        """Validated relative POSIX pathを結合する。"""

        candidate = RemotePosixPath(relative)
        if candidate.is_absolute or any(part == ".." for part in candidate._value.parts):
            raise ValueError(f"remote path must be a descendant-relative path: {relative!r}")
        return RemotePosixPath(self._value / candidate._value)

    @property
    def is_absolute(self) -> bool:
        """pathがabsolute formかを返す。"""

        return self._value.is_absolute()

    @property
    def parent(self) -> RemotePosixPath:
        """親remote pathを返す。"""

        return RemotePosixPath(self._value.parent)

    @property
    def name(self) -> str:
        """末尾path componentを返す。"""

        return self._value.name

    def shell_quote(self) -> str:
        """POSIX shell commandへ渡すためにpathをquoteする。"""

        return shlex.quote(str(self))


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

    async def expand(self, expression: RemotePosixPath) -> RemotePosixPath:
        raw_expression = str(expression)
        if "$" not in raw_expression and not raw_expression.startswith("~"):
            return expression
        rc, stdout, stderr = await self._transport.run('printf "__HOME__=%s\\n" "$HOME"; env')
        if rc != 0 or not stdout:
            raise RuntimeError(f"Failed to resolve remote path: {expression}: {stderr or stdout}")
        return RemotePosixPath(_expand_shell_path(raw_expression, _parse_env(stdout)))

    async def file_exists(self, remote_path: RemotePosixPath) -> bool:
        command = f"test -f {remote_path.shell_quote()}"
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


__all__ = ["RemotePathResolver", "RemotePosixPath", "RemoteProjectLocator"]
