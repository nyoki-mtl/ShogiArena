"""USI engine configuration parsing, normalization, and path resolution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from omegaconf import DictConfig, OmegaConf
from pydantic import ValidationError

from shogiarena._core.platform.engine_runtime.config_parsing import (
    _has_unresolved_placeholder,
    _normalize_optional_positive_int,
    _normalize_optional_str,
    _normalize_optional_str_list,
    _render_template,
    _UsiEngineMappingInput,
    normalize_engine_args,
    to_bool,
    to_env_dict,
    to_float,
    to_string_dict,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.paths import maybe_resolve_path_option, resolve_path_like
from shogiarena._core.shared.kernel.serialization import json_serialize


@dataclass(slots=True)
class UsiEngineConfig:
    """Normalized configuration for launching a USI engine."""

    name: str
    engine_path: str | None = None
    working_directory: str | None = None
    engine_args: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    options: JsonObject = field(default_factory=dict)
    go_options: JsonObject = field(default_factory=dict)
    artifact: str | None = None
    build_options: JsonObject = field(default_factory=dict)
    is_early_ponder_enabled: bool = False
    handshake_timeout: float | None = None
    mate_default_ply_limit: int | None = None
    mate_default_node_limit: int | None = None
    is_mate_default_infinite: bool = False
    should_mate_wait_for_bestmove: bool = False
    isready_sync_strategy: str = "direct"
    isready_lock_key: str | None = None
    isready_lock_template: str | None = None
    isready_lock_check_key: str | None = None
    isready_lock_check_template: str | None = None
    isready_lock_check_templates: tuple[str, ...] = ()
    should_skip_isready_lock_if_exists: bool = False
    _raw_engine_path: str | None = field(default=None, repr=False, compare=False)
    _raw_working_directory: str | None = field(default=None, repr=False, compare=False)
    _raw_options: JsonObject = field(default_factory=dict, repr=False, compare=False)

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

        mapping: JsonObject = {str(key): json_serialize(value) for key, value in raw_mapping.items()}

        instance = cls.from_mapping(mapping, default_name=str(config_path.stem))
        return instance.resolve_paths(output_dir=output_dir, engine_dir=engine_dir)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, JsonValue], *, default_name: str | None = None) -> UsiEngineConfig:
        try:
            parsed = _UsiEngineMappingInput.model_validate(mapping)
        except ValidationError as exc:
            raise TypeError(f"Invalid engine config mapping: {exc}") from exc

        name = parsed.name or default_name or "engine"
        if not name:
            raise ValueError("engine config requires a non-empty name")
        engine_path = parsed.engine_path
        artifact_str = parsed.artifact
        if engine_path is None and artifact_str is None:
            raise ValueError("engine config requires either 'engine_path' or 'artifact'")

        working_dir = parsed.working_directory

        engine_args = normalize_engine_args(parsed.engine_args)

        environment = to_env_dict(parsed.env)

        options = to_string_dict(parsed.options, field="options")

        go_options = to_string_dict(parsed.go_options, field="go_options")

        build_options = to_string_dict(parsed.build_options, field="build_options")
        is_early_ponder_enabled = to_bool(parsed.is_early_ponder_enabled, field="enable_early_ponder")
        handshake_timeout = to_float(parsed.handshake_timeout, field="handshake_timeout")
        if handshake_timeout is not None and handshake_timeout <= 0:
            raise ValueError("handshake_timeout must be positive")
        mate_default_ply_limit = _normalize_optional_positive_int(
            parsed.mate_default_ply_limit, field="mate_default_ply_limit"
        )
        mate_default_node_limit = _normalize_optional_positive_int(
            parsed.mate_default_node_limit, field="mate_default_node_limit"
        )
        is_mate_default_infinite = to_bool(parsed.is_mate_default_infinite, field="mate_default_infinite")
        should_mate_wait_for_bestmove = to_bool(parsed.should_mate_wait_for_bestmove, field="mate_wait_for_bestmove")
        if mate_default_ply_limit is not None and mate_default_node_limit is not None:
            raise ValueError("Specify only one of mate_default_ply_limit or mate_default_node_limit")
        if is_mate_default_infinite and (mate_default_ply_limit is not None or mate_default_node_limit is not None):
            raise ValueError(
                "mate_default_infinite must not be combined with mate_default_ply_limit/mate_default_node_limit"
            )
        isready_sync_strategy_raw = parsed.isready_sync_strategy.strip().lower() or "direct"
        if isready_sync_strategy_raw == "direct":
            isready_sync_strategy: str = "direct"
        elif isready_sync_strategy_raw == "wait":
            isready_sync_strategy = "wait"
        elif isready_sync_strategy_raw == "stop":
            isready_sync_strategy = "stop"
        else:
            raise ValueError("isready_sync_strategy must be one of: direct, wait, stop")
        isready_lock_key = _normalize_optional_str(parsed.isready_lock_key, field="isready_lock_key")
        isready_lock_template = _normalize_optional_str(parsed.isready_lock_template, field="isready_lock_template")
        isready_lock_check_key = _normalize_optional_str(parsed.isready_lock_check_key, field="isready_lock_check_key")
        isready_lock_check_template = _normalize_optional_str(
            parsed.isready_lock_check_template, field="isready_lock_check_template"
        )
        isready_lock_check_templates = _normalize_optional_str_list(
            parsed.isready_lock_check_templates, field="isready_lock_check_templates"
        )
        if isready_lock_key and isready_lock_template:
            raise ValueError("Specify only one of isready_lock_key or isready_lock_template")
        if isready_lock_check_key and isready_lock_check_template:
            raise ValueError("Specify only one of isready_lock_check_key or isready_lock_check_template")
        if isready_lock_check_templates and (isready_lock_check_key or isready_lock_check_template):
            raise ValueError("isready_lock_check_templates must not be combined with isready_lock_check_key/template")
        should_skip_isready_lock_if_exists = to_bool(
            parsed.should_skip_isready_lock_if_exists,
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
            is_early_ponder_enabled=is_early_ponder_enabled,
            handshake_timeout=handshake_timeout,
            mate_default_ply_limit=mate_default_ply_limit,
            mate_default_node_limit=mate_default_node_limit,
            is_mate_default_infinite=is_mate_default_infinite,
            should_mate_wait_for_bestmove=should_mate_wait_for_bestmove,
            isready_sync_strategy=isready_sync_strategy,
            isready_lock_key=isready_lock_key,
            isready_lock_template=isready_lock_template,
            isready_lock_check_key=isready_lock_check_key,
            isready_lock_check_template=isready_lock_check_template,
            isready_lock_check_templates=isready_lock_check_templates,
            should_skip_isready_lock_if_exists=should_skip_isready_lock_if_exists,
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
            key: json_serialize(maybe_resolve_path_option(key, value, output_dir=output_dir, engine_dir=engine_dir))
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
        name: str | None = None,
        engine_path: str | None = None,
        working_directory: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
        go_options: Mapping[str, JsonValue] | None = None,
        output_dir: Path | None = None,
        engine_dir: Path | None = None,
        is_early_ponder_enabled: bool | None = None,
    ) -> UsiEngineConfig:
        new_raw_options = self._raw_options.copy()
        new_resolved_options = self.options.copy()
        if options is not None:
            option_overrides = to_string_dict(options, field="options overrides")
            for key, value in option_overrides.items():
                new_raw_options[key] = value
                new_resolved_options[key] = json_serialize(
                    maybe_resolve_path_option(
                        key,
                        value,
                        output_dir=output_dir,
                        engine_dir=engine_dir,
                    )
                )
        new_go_options = self.go_options.copy()
        if go_options is not None:
            go_option_overrides = to_string_dict(go_options, field="go_options overrides")
            for key, value in go_option_overrides.items():
                new_go_options[key] = value
        new_is_early_ponder_enabled = self.is_early_ponder_enabled
        if is_early_ponder_enabled is not None:
            new_is_early_ponder_enabled = to_bool(is_early_ponder_enabled, field="enable_early_ponder overrides")

        next_name = self.name if name is None else name
        next_engine_path = self.engine_path if engine_path is None else engine_path
        next_working_directory = self.working_directory if working_directory is None else working_directory
        next_raw_engine_path = self._raw_engine_path if engine_path is None else engine_path
        next_raw_working_directory = self._raw_working_directory if working_directory is None else working_directory

        return replace(
            self,
            name=next_name,
            engine_path=next_engine_path,
            working_directory=next_working_directory,
            options=new_resolved_options,
            go_options=new_go_options,
            is_early_ponder_enabled=new_is_early_ponder_enabled,
            _raw_engine_path=next_raw_engine_path,
            _raw_working_directory=next_raw_working_directory,
            _raw_options=new_raw_options,
        )

    def resolve_isready_lock_key(self) -> UsiEngineConfig:
        resolved = self
        if self.isready_lock_template is not None:
            resolved = resolved._resolve_isready_lock_template()
        if self.isready_lock_check_template is not None or self.isready_lock_check_templates:
            resolved = resolved._resolve_isready_check_templates()
        return resolved

    def _template_mapping(self) -> JsonObject:
        mapping: JsonObject = dict(self.options)
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
