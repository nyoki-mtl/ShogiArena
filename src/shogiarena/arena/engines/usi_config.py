"""Configuration loader for USI engines."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, TypeAlias

from omegaconf import DictConfig, OmegaConf

from shogiarena.utils.common.paths import maybe_resolve_path_option, resolve_path_like
from shogiarena.utils.types.coerce import coerce_str_list, is_strict_int

BoolLike: TypeAlias = str | int | float | bool | None
FloatLike: TypeAlias = str | int | float | None
StringListLike: TypeAlias = str | list[str] | tuple[str, ...] | None


def convert_to_string_dict(mapping: object, *, field: str) -> dict[str, object]:
    if not isinstance(mapping, Mapping):
        raise TypeError(f"{field} must be a mapping; got {type(mapping).__name__}")
    return {str(key): value for key, value in mapping.items()}


def convert_to_env_dict(mapping: Mapping[object, object]) -> dict[str, str]:
    env = convert_to_string_dict(mapping, field="env")
    return {key: str(value) for key, value in env.items()}


def convert_to_bool(value: BoolLike, *, field: str) -> bool:
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


def convert_to_float(value: FloatLike, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        try:
            return float(trimmed)
        except ValueError as exc:
            raise TypeError(f"{field} must be a float value; got {value!r}") from exc
    raise TypeError(f"{field} must be a float-compatible value; got {type(value).__name__}")


def normalize_engine_args(raw_args: StringListLike) -> tuple[str, ...]:
    if raw_args is None:
        return ()
    if isinstance(raw_args, str | bytes):
        raise TypeError("engine_args must be an iterable of arguments, not a string")
    try:
        return tuple(str(arg) for arg in raw_args)
    except TypeError as exc:  # e.g. not iterable
        raise TypeError("engine_args must be an iterable of arguments") from exc


def _normalize_optional_str(value: str | None, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string; got {type(value).__name__}")
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed


def _normalize_optional_str_list(value: StringListLike, *, field: str) -> tuple[str, ...]:
    return tuple(coerce_str_list(value, field=field))


def _normalize_optional_positive_int(value: FloatLike, *, field: str) -> int | None:
    if value is None:
        return None
    if is_strict_int(value):
        parsed: int = value  # type: ignore[assignment]  # is_strict_int guarantees int
    elif isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        try:
            parsed = int(trimmed)
        except ValueError as exc:
            raise TypeError(f"{field} must be a positive integer; got {value!r}") from exc
    else:
        raise TypeError(f"{field} must be a positive integer; got {type(value).__name__}")
    if parsed <= 0:
        raise ValueError(f"{field} must be > 0")
    return parsed


_TEMPLATE_PATTERN = re.compile(r"{([^{}]+)}")


def _render_template(template: str, mapping: Mapping[str, object]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = mapping.get(key)
        if value is None:
            return match.group(0)
        return str(value)

    return _TEMPLATE_PATTERN.sub(replace, template)


def _has_unresolved_placeholder(value: str) -> bool:
    return _TEMPLATE_PATTERN.search(value) is not None


@dataclass(slots=True)
class UsiEngineConfig:
    """Normalized configuration for launching a USI engine."""

    name: str
    engine_path: str | None = None
    working_directory: str | None = None
    engine_args: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    options: dict[str, object] = field(default_factory=dict)
    go_options: dict[str, object] = field(default_factory=dict)
    artifact: str | None = None
    build_options: dict[str, object] = field(default_factory=dict)
    enable_early_ponder: bool = False
    handshake_timeout: float | None = None
    mate_default_ply_limit: int | None = None
    mate_default_node_limit: int | None = None
    mate_default_infinite: bool = False
    mate_wait_for_bestmove: bool = False
    isready_sync_strategy: Literal["direct", "wait", "stop"] = "direct"
    isready_lock_key: str | None = None
    isready_lock_template: str | None = None
    isready_lock_check_key: str | None = None
    isready_lock_check_template: str | None = None
    isready_lock_check_templates: tuple[str, ...] = ()
    isready_lock_skip_if_exists: bool = False
    _raw_engine_path: str | None = field(default=None, repr=False, compare=False)
    _raw_working_directory: str | None = field(default=None, repr=False, compare=False)
    _raw_options: dict[str, object] = field(default_factory=dict, repr=False, compare=False)

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
        handshake_timeout = convert_to_float(mapping.get("handshake_timeout"), field="handshake_timeout")
        if handshake_timeout is not None and handshake_timeout <= 0:
            raise ValueError("handshake_timeout must be positive")
        mate_default_ply_limit = _normalize_optional_positive_int(
            mapping.get("mate_default_ply_limit"), field="mate_default_ply_limit"
        )
        mate_default_node_limit = _normalize_optional_positive_int(
            mapping.get("mate_default_node_limit"), field="mate_default_node_limit"
        )
        mate_default_infinite = convert_to_bool(
            mapping.get("mate_default_infinite", False), field="mate_default_infinite"
        )
        mate_wait_for_bestmove = convert_to_bool(
            mapping.get("mate_wait_for_bestmove", False), field="mate_wait_for_bestmove"
        )
        if mate_default_ply_limit is not None and mate_default_node_limit is not None:
            raise ValueError("Specify only one of mate_default_ply_limit or mate_default_node_limit")
        if mate_default_infinite and (mate_default_ply_limit is not None or mate_default_node_limit is not None):
            raise ValueError(
                "mate_default_infinite must not be combined with mate_default_ply_limit/mate_default_node_limit"
            )
        raw_isready_sync_strategy = mapping.get("isready_sync_strategy", "direct")
        if not isinstance(raw_isready_sync_strategy, str):
            raise TypeError("isready_sync_strategy must be a string")
        isready_sync_strategy = raw_isready_sync_strategy.strip().lower() or "direct"
        if isready_sync_strategy not in {"direct", "wait", "stop"}:
            raise ValueError("isready_sync_strategy must be one of: direct, wait, stop")
        isready_lock_key = _normalize_optional_str(mapping.get("isready_lock_key"), field="isready_lock_key")
        isready_lock_template = _normalize_optional_str(
            mapping.get("isready_lock_template"), field="isready_lock_template"
        )
        isready_lock_check_key = _normalize_optional_str(
            mapping.get("isready_lock_check_key"), field="isready_lock_check_key"
        )
        isready_lock_check_template = _normalize_optional_str(
            mapping.get("isready_lock_check_template"), field="isready_lock_check_template"
        )
        isready_lock_check_templates = _normalize_optional_str_list(
            mapping.get("isready_lock_check_templates"), field="isready_lock_check_templates"
        )
        if isready_lock_key and isready_lock_template:
            raise ValueError("Specify only one of isready_lock_key or isready_lock_template")
        if isready_lock_check_key and isready_lock_check_template:
            raise ValueError("Specify only one of isready_lock_check_key or isready_lock_check_template")
        if isready_lock_check_templates and (isready_lock_check_key or isready_lock_check_template):
            raise ValueError("isready_lock_check_templates must not be combined with isready_lock_check_key/template")
        isready_lock_skip_if_exists = convert_to_bool(
            mapping.get("isready_lock_skip_if_exists", False),
            field="isready_lock_skip_if_exists",
        )

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
            handshake_timeout=handshake_timeout,
            mate_default_ply_limit=mate_default_ply_limit,
            mate_default_node_limit=mate_default_node_limit,
            mate_default_infinite=mate_default_infinite,
            mate_wait_for_bestmove=mate_wait_for_bestmove,
            isready_sync_strategy=isready_sync_strategy,
            isready_lock_key=isready_lock_key,
            isready_lock_template=isready_lock_template,
            isready_lock_check_key=isready_lock_check_key,
            isready_lock_check_template=isready_lock_check_template,
            isready_lock_check_templates=isready_lock_check_templates,
            isready_lock_skip_if_exists=isready_lock_skip_if_exists,
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
        options: Mapping[str, object] | None = None,
        go_options: Mapping[str, object] | None = None,
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

    def resolve_isready_lock_key(self) -> UsiEngineConfig:
        resolved = self
        if self.isready_lock_template is not None:
            resolved = resolved._resolve_isready_lock_template()
        if self.isready_lock_check_template is not None or self.isready_lock_check_templates:
            resolved = resolved._resolve_isready_check_templates()
        return resolved

    def _template_mapping(self) -> dict[str, object]:
        mapping: dict[str, object] = dict(self.options)
        for key, value in self.build_options.items():
            mapping.setdefault(key, value)
        mapping.update(
            {
                "name": self.name,
                "engine_path": self.engine_path or "",
                "artifact": self.artifact or "",
            }
        )
        return mapping

    def _resolve_isready_lock_template(self) -> UsiEngineConfig:
        template = self.isready_lock_template
        if template is None:
            return self
        mapping = self._template_mapping()
        resolved_value = _render_template(template, mapping).strip()
        if not resolved_value:
            return replace(self, isready_lock_key=None)
        return replace(self, isready_lock_key=resolved_value)

    def _resolve_isready_check_templates(self) -> UsiEngineConfig:
        templates: list[str] = []
        if self.isready_lock_check_template is not None:
            templates.append(self.isready_lock_check_template)
        templates.extend(self.isready_lock_check_templates)
        if not templates:
            return self
        mapping = self._template_mapping()
        resolved_values: list[str] = []
        for template in templates:
            rendered = _render_template(template, mapping).strip()
            if not rendered:
                continue
            if _has_unresolved_placeholder(rendered):
                continue
            resolved_values.append(rendered)
        if not resolved_values:
            return replace(self, isready_lock_check_key=None, isready_lock_check_templates=())
        return replace(
            self,
            isready_lock_check_key=resolved_values[0],
            isready_lock_check_templates=tuple(resolved_values),
        )
