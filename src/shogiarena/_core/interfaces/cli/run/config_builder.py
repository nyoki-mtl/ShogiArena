"""Helpers for building config payloads from CLI inputs."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from shogiarena._core.interfaces.cli.main import CliArgumentError
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.paths import resolve_path_like
from shogiarena._core.shared.kernel.serialization import json_serialize

from .config_overrides import apply_override, apply_section_overrides, parse_scalar

ConfigPayload = JsonObject

_DIRECT_ENGINE_RUNTIME_KEYS = (
    "working_directory",
    "engine_args",
    "environment",
    "go_options",
    "build_options",
    "enable_early_ponder",
    "handshake_timeout",
    "mate_default_ply_limit",
    "mate_default_node_limit",
    "mate_default_infinite",
    "mate_wait_for_bestmove",
    "isready_sync_strategy",
    "isready_lock_key",
    "isready_lock_template",
    "isready_lock_check_key",
    "isready_lock_check_template",
    "isready_lock_check_templates",
    "isready_lock_skip_if_exists",
    "io",
    "option_validation",
)
_TOURNAMENT_ENGINE_KEYS = (
    "options",
    "options_overlays",
    "path_options",
    "time_control",
    "name_style",
    "instance_id",
    "cpu_affinity",
)


def build_cli_config_payload(
    *,
    base: ConfigPayload | None,
    engines_tokens: list[list[str]] | None,
    sections: dict[str, list[str] | None],
    experiment_name: str | None,
    default_experiment: str,
    label: str,
) -> ConfigPayload:
    payload: ConfigPayload = dict(base or {})
    if experiment_name:
        payload["experiment_name"] = experiment_name
    elif "experiment_name" not in payload:
        payload["experiment_name"] = default_experiment

    output_dir = _resolve_output_dir(payload.get("output_dir"))

    if engines_tokens:
        engines = [parse_engine_tokens(tokens) for tokens in engines_tokens]
        prepare_engine_payloads(engines, label=label, output_dir=output_dir)
        payload["engines"] = engines

    for section, tokens in sections.items():
        if tokens:
            apply_section_overrides(payload, section, tokens)

    _normalize_rules_paths(payload)
    return payload


def parse_engine_tokens(tokens: list[str]) -> ConfigPayload:
    if not tokens:
        raise CliArgumentError("engine tokens are empty")
    engine: ConfigPayload = {}
    for raw in tokens:
        if "=" not in raw:
            raise CliArgumentError(f"invalid engine token (expected KEY=VALUE): {raw}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise CliArgumentError(f"invalid engine key: {raw}")
        parsed = parse_scalar(value.strip(), label=f"engine value for {key}")
        if key in {"options_overlays", "engine_args"}:
            _append_list(engine, key, parsed)
            continue
        apply_override(engine, key, parsed)
    return engine


def materialize_engine_configs(
    engines: list[ConfigPayload],
    *,
    label: str,
    output_dir: Path | None = None,
) -> None:
    base_output_dir = output_dir or project_dirs.output_dir

    for idx, engine in enumerate(engines, 1):
        if isinstance(engine.get("artifact"), str) and str(engine["artifact"]).strip():
            continue
        if isinstance(engine.get("engine_path"), str) and str(engine["engine_path"]).strip():
            engine["engine_path"] = str(Path(resolve_path_like(str(engine["engine_path"]))).resolve())
            continue

        binary_path = engine.pop("path", None) or engine.pop("binary", None)
        if binary_path is None:
            raise CliArgumentError("engine must specify artifact, engine_path, or path/binary")

        resolved_path = Path(resolve_path_like(str(binary_path))).resolve()
        if not resolved_path.exists():
            raise CliArgumentError(f"engine binary not found: {resolved_path}")
        if not resolved_path.is_file():
            raise CliArgumentError(f"engine binary is not a file: {resolved_path}")

        name = str(engine.pop("name", None) or f"engine-{idx}")
        working_directory_raw = engine.pop("working_directory", resolved_path.parent)
        runtime_payload: ConfigPayload = {
            "name": name,
            "engine_path": str(resolved_path),
            "working_directory": str(Path(resolve_path_like(str(working_directory_raw))).resolve()),
        }
        for key in _DIRECT_ENGINE_RUNTIME_KEYS:
            _merge_if_present(runtime_payload, engine, key)

        payload: ConfigPayload = {"name": name}
        for key in _TOURNAMENT_ENGINE_KEYS:
            _merge_if_present(payload, engine, key)
        if engine:
            extras = ", ".join(sorted(engine))
            raise CliArgumentError(f"unsupported engine key(s): {extras}")

        generated_config = _write_direct_engine_config(
            runtime_payload,
            output_dir=base_output_dir,
            label=label,
            index=idx,
            name=name,
        )
        payload["engine_path"] = str(generated_config)

        engine.clear()
        engine.update(payload)


def prepare_engine_payloads(
    engines: list[ConfigPayload],
    *,
    label: str,
    output_dir: Path | None = None,
) -> None:
    ensure_engine_names(engines)
    _normalize_engine_overlays(engines)
    materialize_engine_configs(engines, label=label, output_dir=output_dir)


def _write_direct_engine_config(
    payload: ConfigPayload,
    *,
    output_dir: Path,
    label: str,
    index: int,
    name: str,
) -> Path:
    config_dir = output_dir / "generated_engine_configs" / _safe_filename_part(label)
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / f"{index:02d}-{_safe_filename_part(name)}.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return config_path


def _safe_filename_part(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return normalized.strip(".-") or "engine"


def _resolve_output_dir(raw: JsonValue | Path | None) -> Path:
    if raw is None:
        return project_dirs.output_dir
    return Path(resolve_path_like(str(raw)))


def _merge_if_present(payload: ConfigPayload, engine: ConfigPayload, key: str) -> None:
    if key in engine:
        payload[key] = engine.pop(key)


def _append_list(target: ConfigPayload, key: str, value: JsonValue) -> None:
    if key not in target or target[key] is None:
        target[key] = []
    current = target[key]
    if not isinstance(current, list):
        raise CliArgumentError(f"{key} must be a list")
    if isinstance(value, list):
        current.extend(json_serialize(item) for item in value)
    else:
        current.append(json_serialize(value))


def ensure_engine_names(engines: list[ConfigPayload]) -> None:
    for idx, engine in enumerate(engines, 1):
        if not engine.get("name"):
            engine["name"] = f"engine-{idx}"


def _normalize_engine_overlays(engines: list[ConfigPayload]) -> None:
    for engine in engines:
        overlays = engine.get("options_overlays")
        if overlays is None:
            continue
        if isinstance(overlays, str):
            items = [overlays]
        elif isinstance(overlays, list):
            items = overlays
        else:
            raise CliArgumentError("options_overlays must be a string or list of strings")
        resolved: list[str] = []
        for item in items:
            if not isinstance(item, str) or not item.strip():
                raise CliArgumentError("options_overlays entries must be non-empty strings")
            resolved.append(str(Path(resolve_path_like(item)).resolve()))
        engine["options_overlays"] = resolved


def _normalize_rules_paths(payload: ConfigPayload) -> None:
    rules = payload.get("rules")
    if not isinstance(rules, dict):
        return
    rules_map: JsonObject = {str(key): json_serialize(value) for key, value in rules.items()}
    initial_positions_raw = rules_map.get("initial_positions")
    if not isinstance(initial_positions_raw, dict):
        return
    initial_positions: JsonObject = {str(key): json_serialize(value) for key, value in initial_positions_raw.items()}
    source = initial_positions.get("source")
    if isinstance(source, str) and source.strip():
        initial_positions["source"] = str(Path(resolve_path_like(source)).resolve())
        rules_map["initial_positions"] = initial_positions
        payload["rules"] = rules_map
