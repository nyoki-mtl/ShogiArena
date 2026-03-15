from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError

from shogiarena._core.platform.settings.platform_paths import (
    default_engine_dir,
    default_output_dir,
    default_settings_path,
)
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.settings_loading.settings_models import (
    ArenaSettings,
    OpenBenchSettings,
    RepoSettings,
)
from shogiarena._core.shared.kernel.settings_loading.settings_parsing_models import _ArenaSettingsModel

LOGGER = logging.getLogger("shogiarena._core.shared.kernel.settings")


def _load_yaml(path: Path) -> JsonObject:
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, Mapping):
        raise TypeError(f"Expected mapping in settings file: {path}")
    return to_json_object(data)


def _validate_settings_payload(data: Mapping[str, object]) -> dict[str, object]:
    try:
        parsed = _ArenaSettingsModel.model_validate(data)
    except ValidationError as exc:
        raise TypeError(f"Invalid settings file: {exc}") from exc
    validated = json_serialize(parsed.model_dump(mode="python"))
    if not isinstance(validated, dict):
        raise TypeError("validated settings payload must be a mapping")
    return validated


def _load_repos(data: Mapping[str, object]) -> dict[str, RepoSettings]:
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
        value_map = {str(item_key): item_value for item_key, item_value in value.items()}
        path_raw = value_map.get("path")
        if not path_raw:
            raise ValueError(f"settings.repos.{name}.path is required")
        path = Path(str(path_raw)).expanduser()
        url_val = value_map.get("url")
        url = str(url_val) if url_val else None
        build_config_raw = value_map.get("build_config")
        build_config = Path(str(build_config_raw)).expanduser() if build_config_raw else None
        repos[name] = RepoSettings(name=name, path=path, url=url, build_config=build_config)
    return repos


def _load_overlays(data: Mapping[str, object]) -> dict[str, Path]:
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


def _load_openbench(data: Mapping[str, object]) -> OpenBenchSettings | None:
    raw = data.get("openbench")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise TypeError("settings.openbench must be a mapping")
    raw_map = {str(key): value for key, value in raw.items()}
    server_val = raw_map.get("server")
    username_val = raw_map.get("username")
    password_env_val = raw_map.get("password_env")
    server = str(server_val).strip() if server_val else None
    username = str(username_val).strip() if username_val else None
    password_env = str(password_env_val).strip() if password_env_val else "OPENBENCH_PASSWORD"
    return OpenBenchSettings(server=server, username=username, password_env=password_env)


def load_settings(
    *,
    root: Path | None = None,
    should_require_settings: bool = False,
    should_suppress_warning: bool = False,
    settings_path_provider: Callable[[], Path] | None = None,
) -> ArenaSettings:
    """Load runtime settings, resolving from settings.yaml.

    ``root`` overrides the output_dir for the current process when provided.

    When ``should_require_settings`` is True and settings.yaml is missing, raises FileNotFoundError.
    When ``should_require_settings`` is False and settings.yaml is missing, uses default values and logs a warning
    (unless ``should_suppress_warning`` is True, e.g., when running init command).
    """

    settings_path_factory = settings_path_provider or default_settings_path
    settings_path = Path(settings_path_factory()).expanduser()
    data: dict[str, object] = {}
    if settings_path.exists():
        data = _validate_settings_payload(_load_yaml(settings_path))
    elif should_require_settings and root is None:
        raise FileNotFoundError(
            f"settings.yaml not found at {settings_path}. Run `shogiarena config init` to create one."
        )
    elif not settings_path.exists() and not should_suppress_warning:
        LOGGER.warning(
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
    payload: dict[str, object] = {
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


__all__ = ["load_settings", "validate_overlays", "write_settings_file"]
