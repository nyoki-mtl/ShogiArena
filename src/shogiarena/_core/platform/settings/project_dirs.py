"""Process-wide runtime directories shared across bounded contexts."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from shogiarena._core.platform.settings.platform_paths import (
    default_engine_dir as _default_engine_dir_path,
)
from shogiarena._core.platform.settings.platform_paths import (
    default_output_dir as _default_output_dir_path,
)
from shogiarena._core.platform.settings.platform_paths import (
    default_settings_path,
)


class _ProjectDirsSettingsPort(Protocol):
    @property
    def output_dir(self) -> Path: ...

    @property
    def engine_dir(self) -> Path: ...

    @property
    def repos(self) -> Mapping[str, object]: ...

    @property
    def overlays(self) -> Mapping[str, Path]: ...

    @property
    def settings_path(self) -> Path: ...


def _default_output_dir() -> Path:
    """Default output directory when settings.yaml is not configured."""

    return _default_output_dir_path()


def _default_engine_dir() -> Path:
    """Default engine cache directory when settings.yaml is not configured."""

    return _default_engine_dir_path()


output_dir = _default_output_dir()
engine_dir = _default_engine_dir()
repos: dict[str, object] = {}
overlays: dict[str, Path] = {}
settings_path = default_settings_path()
log_root_dir = output_dir / "logs"


def _apply_settings(settings: _ProjectDirsSettingsPort) -> None:
    global output_dir
    global engine_dir
    global repos
    global overlays
    global log_root_dir
    global settings_path

    output_dir = settings.output_dir
    engine_dir = settings.engine_dir
    repos = dict(settings.repos)
    overlays = dict(settings.overlays)
    log_root_dir = output_dir / "logs"
    settings_path = settings.settings_path


__all__ = [
    "engine_dir",
    "log_root_dir",
    "output_dir",
    "overlays",
    "repos",
    "settings_path",
    "_apply_settings",
]
