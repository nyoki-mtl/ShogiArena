"""Configuration loader for USI engines."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from omegaconf import DictConfig, OmegaConf

from shogiarena.utils.common.paths import maybe_resolve_path_option, resolve_path_like


def convert_to_string_dict(mapping: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(mapping, Mapping):
        raise TypeError(f"{field} must be a mapping; got {type(mapping).__name__}")
    return {str(key): value for key, value in mapping.items()}


def convert_to_env_dict(mapping: Mapping[Any, Any]) -> dict[str, str]:
    env = convert_to_string_dict(mapping, field="env")
    return {key: str(value) for key, value in env.items()}


def convert_to_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int | float):
        if value == 0 or value == 0.0:
            return False
        if value == 1 or value == 1.0:
            return True
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0"}:
            return False
    raise TypeError(f"{field} must be a boolean-compatible value; got {type(value).__name__}")


def normalize_engine_args(raw_args: Any) -> tuple[str, ...]:
    if raw_args is None:
        return ()
    if isinstance(raw_args, str | bytes):
        raise TypeError("engine_args must be an iterable of arguments, not a string")
    try:
        return tuple(str(arg) for arg in raw_args)
    except TypeError as exc:  # e.g. not iterable
        raise TypeError("engine_args must be an iterable of arguments") from exc


@dataclass(slots=True)
class UsiEngineConfig:
    """Normalized configuration for launching a USI engine."""

    name: str
    engine_path: str | None = None
    working_directory: str | None = None
    engine_args: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    go_options: dict[str, Any] = field(default_factory=dict)
    artifact: str | None = None
    build_options: dict[str, Any] = field(default_factory=dict)
    enable_early_ponder: bool = False
    _raw_engine_path: str | None = field(default=None, repr=False, compare=False)
    _raw_working_directory: str | None = field(default=None, repr=False, compare=False)
    _raw_options: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        output_dir: Path | None = None,
        engine_dir: Path | None = None,
    ) -> UsiEngineConfig:
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Engine config file not found: {config_path}")
        loaded = OmegaConf.load(config_path)
        if isinstance(loaded, DictConfig):
            raw_mapping = OmegaConf.to_container(loaded, resolve=True)
        else:
            raw_mapping = loaded
        if not isinstance(raw_mapping, Mapping):
            raise TypeError(
                f"Engine config must load into a mapping; got {type(raw_mapping).__name__} from {config_path}"
            )
        if not isinstance(raw_mapping, dict):
            raise TypeError(f"Engine config mapping must be a dict with string keys; got {type(raw_mapping).__name__}")

        mapping = {str(key): value for key, value in raw_mapping.items()}

        instance = cls.from_mapping(mapping, default_name=str(config_path.stem))
        return instance.resolve_paths(output_dir=output_dir, engine_dir=engine_dir)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any], *, default_name: str | None = None) -> UsiEngineConfig:
        name = str(mapping.get("name", default_name or "engine"))
        if not name:
            raise ValueError("engine config requires a non-empty name")
        raw_engine_path = mapping.get("engine_path")
        engine_path = str(raw_engine_path) if raw_engine_path is not None else None
        artifact = mapping.get("artifact")
        artifact_str = str(artifact) if artifact is not None else None
        if engine_path is None and artifact_str is None:
            raise ValueError("engine config requires either 'engine_path' or 'artifact'")

        raw_working_dir = mapping.get("working_dir") or mapping.get("working_directory")
        working_dir = str(raw_working_dir) if raw_working_dir is not None else None

        engine_args = normalize_engine_args(mapping.get("engine_args"))

        environment = convert_to_env_dict(mapping.get("env", {}))

        options = convert_to_string_dict(mapping.get("options", {}), field="options")

        go_options = convert_to_string_dict(mapping.get("go_options", {}), field="go_options")

        build_options = convert_to_string_dict(mapping.get("build_options", {}), field="build_options")
        enable_early_ponder = convert_to_bool(mapping.get("enable_early_ponder", False), field="enable_early_ponder")

        return cls(
            name=name,
            engine_path=engine_path,
            working_directory=working_dir,
            engine_args=engine_args,
            environment=environment,
            options=options.copy(),
            go_options=go_options.copy(),
            artifact=artifact_str,
            build_options=build_options.copy(),
            enable_early_ponder=enable_early_ponder,
            _raw_engine_path=engine_path,
            _raw_working_directory=working_dir,
            _raw_options=options.copy(),
        )

    def resolve_paths(
        self,
        *,
        output_dir: Path | None = None,
        engine_dir: Path | None = None,
        extra_placeholders: Mapping[str, str] | None = None,
    ) -> UsiEngineConfig:
        placeholders = dict(extra_placeholders) if extra_placeholders is not None else None
        resolved_engine_path = (
            resolve_path_like(
                self._raw_engine_path,
                output_dir=output_dir,
                engine_dir=engine_dir,
                extra_placeholders=placeholders,
            )
            if self._raw_engine_path is not None
            else None
        )
        resolved_work_dir = (
            resolve_path_like(
                self._raw_working_directory,
                output_dir=output_dir,
                engine_dir=engine_dir,
                extra_placeholders=placeholders,
            )
            if self._raw_working_directory is not None
            else None
        )
        resolved_options = {
            key: maybe_resolve_path_option(key, value, output_dir=output_dir, engine_dir=engine_dir)
            for key, value in self._raw_options.items()
        }
        return replace(
            self,
            engine_path=resolved_engine_path,
            working_directory=resolved_work_dir,
            options=resolved_options,
        )

    def with_overrides(
        self,
        *,
        options: Mapping[str, Any] | None = None,
        go_options: Mapping[str, Any] | None = None,
        output_dir: Path | None = None,
        engine_dir: Path | None = None,
        enable_early_ponder: bool | None = None,
    ) -> UsiEngineConfig:
        new_raw_options = self._raw_options.copy()
        new_resolved_options = self.options.copy()
        if options is not None:
            option_overrides = convert_to_string_dict(options, field="options overrides")
            for key, value in option_overrides.items():
                new_raw_options[key] = value
                new_resolved_options[key] = maybe_resolve_path_option(
                    key,
                    value,
                    output_dir=output_dir,
                    engine_dir=engine_dir,
                )
        new_go_options = self.go_options.copy()
        if go_options is not None:
            go_option_overrides = convert_to_string_dict(go_options, field="go_options overrides")
            for key, value in go_option_overrides.items():
                new_go_options[key] = value
        new_enable_early_ponder = self.enable_early_ponder
        if enable_early_ponder is not None:
            new_enable_early_ponder = convert_to_bool(enable_early_ponder, field="enable_early_ponder overrides")
        return replace(
            self,
            options=new_resolved_options,
            go_options=new_go_options,
            enable_early_ponder=new_enable_early_ponder,
            _raw_options=new_raw_options,
        )
