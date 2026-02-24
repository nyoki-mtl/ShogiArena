"""Runtime settings loader for Shogi Arena.

The runtime looks for ``settings.yaml`` to determine output/engine directories
and registered artifact repositories.
"""

from __future__ import annotations

import os
import platform
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RepoSettings:
    """Repository configuration for artifact builds."""

    name: str
    path: Path
    url: str | None = None
    build_config: Path | None = None


@dataclass(frozen=True)
class ArenaSettings:
    """Resolved runtime settings."""

    output_dir: Path
    engine_dir: Path
    settings_path: Path
    repos: dict[str, RepoSettings]
    github_token: str | None
    overlays: dict[str, Path]
    openbench: OpenBenchSettings | None


@dataclass(frozen=True)
class OpenBenchSettings:
    server: str | None = None
    username: str | None = None
    password_env: str = "OPENBENCH_PASSWORD"


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


def _load_yaml(path: Path) -> Mapping[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, Mapping):
        raise TypeError(f"Expected mapping in settings file: {path}")
    return data


def _load_repos(data: Mapping[str, Any]) -> dict[str, RepoSettings]:
    repos_raw = data.get("repos")
    if repos_raw is None:
        return {}
    if not isinstance(repos_raw, Mapping):
        raise TypeError("settings.repos must be a mapping")
    repos: dict[str, RepoSettings] = {}
    for key, value in repos_raw.items():
        name = str(key)
        if not isinstance(value, Mapping):
            raise TypeError(f"settings.repos.{name} must be a mapping")
        path_raw = value.get("path")
        if not path_raw:
            raise ValueError(f"settings.repos.{name}.path is required")
        path = Path(str(path_raw)).expanduser()
        url_val = value.get("url")
        url = str(url_val) if url_val else None
        build_config_raw = value.get("build_config")
        build_config = Path(str(build_config_raw)).expanduser() if build_config_raw else None
        repos[name] = RepoSettings(name=name, path=path, url=url, build_config=build_config)
    return repos


def _load_overlays(data: Mapping[str, Any]) -> dict[str, Path]:
    overlays_raw = data.get("overlays")
    if overlays_raw is None:
        return {}
    if not isinstance(overlays_raw, Mapping):
        raise TypeError("settings.overlays must be a mapping")
    overlays: dict[str, Path] = {}
    for key, value in overlays_raw.items():
        name = str(key)
        if not value:
            continue
        overlays[name] = Path(str(value)).expanduser()
    return overlays


def _load_openbench(data: Mapping[str, Any]) -> OpenBenchSettings | None:
    raw = data.get("openbench")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise TypeError("settings.openbench must be a mapping")
    server_val = raw.get("server")
    username_val = raw.get("username")
    password_env_val = raw.get("password_env")
    server = str(server_val).strip() if server_val else None
    username = str(username_val).strip() if username_val else None
    password_env = str(password_env_val).strip() if password_env_val else "OPENBENCH_PASSWORD"
    return OpenBenchSettings(server=server, username=username, password_env=password_env)


def load_settings(
    *, root: Path | None = None, require_settings: bool = False, suppress_warning: bool = False
) -> ArenaSettings:
    """Load runtime settings, resolving from settings.yaml.

    ``root`` overrides the output_dir for the current process when provided.

    When ``require_settings`` is True and settings.yaml is missing, raises FileNotFoundError.
    When ``require_settings`` is False and settings.yaml is missing, uses default values and logs a warning
    (unless ``suppress_warning`` is True, e.g., when running init command).
    """

    settings_path = Path(default_settings_path()).expanduser()
    data: Mapping[str, Any] = {}
    if settings_path.exists():
        data = _load_yaml(settings_path)
    elif require_settings and root is None:
        raise FileNotFoundError(
            f"settings.yaml not found at {settings_path}. Run `shogiarena config init` to create one."
        )
    elif not settings_path.exists() and not suppress_warning:
        # Settings file doesn't exist but require_settings is False
        # Log a warning but continue with defaults (unless suppressed, e.g., for init command)
        import logging

        logger = logging.getLogger("shogiarena.utils.common.settings")
        logger.warning(
            "settings.yaml not found at %s. Using default values. "
            "Run `shogiarena config init` to create settings.yaml. "
            "Note: artifact-based engines require repo configuration in settings.yaml.",
            settings_path,
        )

    output_raw = data.get("output_dir")
    output_dir = Path(str(output_raw)).expanduser() if output_raw else default_output_dir()
    if root is not None:
        output_dir = Path(root).expanduser()
    engine_raw = data.get("engine_dir")
    engine_dir = Path(str(engine_raw)).expanduser() if engine_raw else default_engine_dir()

    repos = _load_repos(data)
    overlays = _load_overlays(data)
    openbench = _load_openbench(data)
    github_token_raw = data.get("github_token")
    github_token = str(github_token_raw).strip() if github_token_raw else None

    return ArenaSettings(
        output_dir=output_dir,
        engine_dir=engine_dir,
        settings_path=settings_path,
        repos=repos,
        github_token=github_token,
        overlays=overlays,
        openbench=openbench,
    )


def write_settings_file(
    settings_path: Path,
    *,
    output_dir: Path,
    engine_dir: Path,
    repos: dict[str, RepoSettings] | None = None,
    github_token: str | None = None,
    overlays: dict[str, Path] | None = None,
    openbench: OpenBenchSettings | None = None,
) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "output_dir": str(output_dir),
        "engine_dir": str(engine_dir),
    }
    if github_token:
        payload["github_token"] = github_token
    if repos:
        payload["repos"] = {
            name: {
                "path": str(spec.path),
                **({"url": spec.url} if spec.url else {}),
                **({"build_config": str(spec.build_config)} if spec.build_config else {}),
            }
            for name, spec in repos.items()
        }
    if overlays:
        payload["overlays"] = {name: str(path) for name, path in overlays.items()}
    if openbench is not None:
        payload["openbench"] = {
            "server": openbench.server,
            "username": openbench.username,
            "password_env": openbench.password_env,
        }
    with open(settings_path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=True)


def validate_overlays(settings: ArenaSettings) -> None:
    for name, path in settings.overlays.items():
        if not path.exists():
            raise FileNotFoundError(f"overlay config not found for {name}: {path}")
        raw = _load_yaml(path)
        if not isinstance(raw, Mapping):
            raise TypeError(f"overlay YAML must be a mapping: {path}")
        overlay_opts = raw.get("options") if "options" in raw else raw
        if not isinstance(overlay_opts, Mapping):
            raise TypeError(f"overlay options must be a mapping: {path}")


# Module-level SETTINGS initialization - suppress warning since this is just for
# initial import. Actual settings will be configured via configure_settings() in main().
SETTINGS = load_settings(suppress_warning=True)


def configure_settings(
    *, root: Path | None = None, require_settings: bool = True, suppress_warning: bool = False
) -> ArenaSettings:
    """Reconfigure global settings and propagate them to dependent modules."""

    global SETTINGS
    SETTINGS = load_settings(root=root, require_settings=require_settings, suppress_warning=suppress_warning)
    try:
        from shogiarena.utils.common import project_dirs as _project_dirs

        _project_dirs._apply_settings(SETTINGS)
    except ImportError:
        # project_dirs has not been imported yet; nothing to sync.
        pass
    return SETTINGS
