"""Path placeholder and environment expansion helpers."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TypeVar, overload

_POSIX_ENV_PATTERN = re.compile(r"\$(\w+)|\$\{([^}]+)\}")
_WIN_ENV_PATTERN = re.compile(r"%([^%]+)%")

# Module-level defaults, populated by platform/settings at startup.
_default_output_dir: Path = Path.cwd() / "shogiarena_output"
_default_engine_dir: Path = Path.cwd() / "shogiarena_engines"


def _configure_defaults(*, output_dir: Path, engine_dir: Path) -> None:
    """Set the process-wide default dirs for placeholder resolution.

    Called once by the settings bootstrap; not part of the public API.
    """

    global _default_output_dir, _default_engine_dir
    _default_output_dir = output_dir
    _default_engine_dir = engine_dir


def _expand_env_vars(s: str) -> str:
    """Expand environment variables in a string without using os.path."""

    def _posix_sub(match: re.Match[str]) -> str:
        var = match.group(1) or match.group(2)
        return os.environ.get(var or "", match.group(0))

    s2 = _POSIX_ENV_PATTERN.sub(_posix_sub, s)
    s2 = _WIN_ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(0)), s2)
    return s2


def resolve_path_like(
    s: str,
    *,
    output_dir: Path | None = None,
    engine_dir: Path | None = None,
    extra_placeholders: dict[str, str] | None = None,
) -> str:
    """Resolve project-style placeholders and env/home expansions."""

    raw = s.strip()
    base_output = output_dir or _default_output_dir
    base_engine = engine_dir or _default_engine_dir

    placeholders = {
        "{output_dir}": str(base_output),
        "{engine_dir}": str(base_engine),
    }
    if extra_placeholders:
        placeholders.update(extra_placeholders)

    unknown = {placeholder for placeholder in re.findall(r"\{[^}]+\}", raw) if placeholder not in placeholders}
    if unknown:
        raise ValueError(f"Unknown placeholders in path '{s}': {', '.join(sorted(unknown))}")

    for key, value in placeholders.items():
        raw = raw.replace(key, value)

    raw = _expand_env_vars(raw)
    return str(Path(raw).expanduser())


PATH_OPTION_KEYS: set[str] = {
    "EvalDir",
    "BookDir",
    "Book_File",
    "DNN_Model",
}


def _is_path_option_key(name: str) -> bool:
    """Return True if the USI option key should be treated as a path."""
    return name in PATH_OPTION_KEYS


_PathOptionT = TypeVar("_PathOptionT")


@overload
def maybe_resolve_path_option(
    name: str,
    value: str,
    *,
    output_dir: Path | None = None,
    engine_dir: Path | None = None,
) -> str: ...


@overload
def maybe_resolve_path_option(
    name: str,
    value: _PathOptionT,
    *,
    output_dir: Path | None = None,
    engine_dir: Path | None = None,
) -> _PathOptionT: ...


def maybe_resolve_path_option(
    name: str,
    value: _PathOptionT | str,
    *,
    output_dir: Path | None = None,
    engine_dir: Path | None = None,
) -> _PathOptionT | str:
    """Resolve a USI option value only when the key is path-typed."""

    if not isinstance(value, str):
        return value
    if not _is_path_option_key(name):
        return value
    return resolve_path_like(value, output_dir=output_dir, engine_dir=engine_dir)
