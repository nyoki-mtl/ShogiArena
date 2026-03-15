from __future__ import annotations

import os
import platform
import tempfile
from pathlib import Path


def _config_base_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support"
    # POSIX default (Linux, etc.)
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base


def _data_base_dir() -> Path:
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base) if base else Path.home() / "AppData" / "Local"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base


def default_settings_path() -> Path:
    return _config_base_dir() / "shogiarena" / "settings.yaml"


def default_output_dir() -> Path:
    """Default output directory when settings.yaml is not configured.

    Returns a subdirectory in the current working directory to avoid
    polluting the working directory with output files.
    This allows running tournaments without initialization.
    """

    return Path.cwd() / "shogiarena_output"


def default_engine_dir() -> Path:
    """Default engine cache directory when settings.yaml is not configured.

    Returns a temporary directory for engine binaries.
    This allows running tournaments without initialization.
    """

    return Path(tempfile.gettempdir()) / "shogiarena-engines"


def default_output_dir_for_init() -> Path:
    """Default output directory for 'config init' command.

    Returns platform-standard data directory.
    This is used when initializing settings.yaml.
    """

    return _data_base_dir() / "shogiarena" / "output"


def default_engine_dir_for_init() -> Path:
    """Default engine cache directory for 'config init' command.

    Returns platform-standard data directory.
    This is used when initializing settings.yaml.
    """

    return _data_base_dir() / "shogiarena" / "engines"


__all__ = [
    "default_engine_dir",
    "default_engine_dir_for_init",
    "default_output_dir",
    "default_output_dir_for_init",
    "default_settings_path",
]
