"""Runtime settings loader for Shogi Arena."""

from __future__ import annotations

from pathlib import Path

from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.platform.settings.loader import (
    load_settings as _load_settings_impl,
)
from shogiarena._core.platform.settings.platform_paths import (
    default_settings_path,
)
from shogiarena._core.shared.kernel.paths import _configure_defaults as _configure_path_defaults
from shogiarena._core.shared.kernel.settings_loading.settings_models import ArenaSettings


def load_settings(
    *,
    root: Path | None = None,
    should_require_settings: bool = False,
    should_suppress_warning: bool = False,
) -> ArenaSettings:
    return _load_settings_impl(
        root=root,
        should_require_settings=should_require_settings,
        should_suppress_warning=should_suppress_warning,
        settings_path_provider=default_settings_path,
    )


SETTINGS = load_settings(should_suppress_warning=True)
project_dirs._apply_settings(SETTINGS)
_configure_path_defaults(output_dir=SETTINGS.output_dir, engine_dir=SETTINGS.engine_dir)


def current_settings() -> ArenaSettings:
    """Return the current process-wide settings snapshot."""

    return SETTINGS


def configure_settings(
    *,
    root: Path | None = None,
    should_require_settings: bool = True,
    should_suppress_warning: bool = False,
) -> ArenaSettings:
    """Reconfigure global settings and propagate them to dependent modules."""

    global SETTINGS
    SETTINGS = load_settings(
        root=root,
        should_require_settings=should_require_settings,
        should_suppress_warning=should_suppress_warning,
    )
    project_dirs._apply_settings(SETTINGS)
    _configure_path_defaults(output_dir=SETTINGS.output_dir, engine_dir=SETTINGS.engine_dir)
    return SETTINGS


__all__ = [
    "SETTINGS",
    "configure_settings",
    "current_settings",
    "load_settings",
]
