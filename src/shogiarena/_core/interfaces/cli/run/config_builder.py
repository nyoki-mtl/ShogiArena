"""Helpers for building config payloads from CLI inputs."""

from __future__ import annotations

from pathlib import Path

import yaml

from shogiarena._core.interfaces.cli.main import CliArgumentError
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.paths import resolve_path_like
from shogiarena._core.shared.kernel.run_paths import timestamp_slug
from shogiarena._core.shared.kernel.serialization import json_serialize

from .config_overrides import apply_override, apply_section_overrides, parse_scalar

ConfigPayload = JsonObject


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
        ensure_engine_names(engines)
        _normalize_engine_overlays(engines)
        materialize_engine_configs(engines, label=label, output_dir=output_dir)
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
    base_dir = output_dir or project_dirs.output_dir
    stamp = timestamp_slug()
    config_dir = base_dir / "cli" / "engines" / f"{label}-{stamp}"
    config_dir.mkdir(parents=True, exist_ok=True)

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

        name = str(engine.get("name") or f"engine-{idx}")
        working_directory_raw = engine.pop("working_directory", resolved_path.parent)
        payload: ConfigPayload = {
            "name": name,
            "engine_path": str(resolved_path),
            "working_directory": str(Path(resolve_path_like(str(working_directory_raw))).resolve()),
        }
        _merge_if_present(payload, engine, "engine_args")
        _merge_if_present(payload, engine, "environment")
        _merge_if_present(payload, engine, "go_options")
        _merge_if_present(payload, engine, "enable_early_ponder")
        _merge_if_present(payload, engine, "mate_default_ply_limit")
        _merge_if_present(payload, engine, "mate_default_node_limit")
        _merge_if_present(payload, engine, "mate_default_infinite")
        _merge_if_present(payload, engine, "mate_wait_for_bestmove")
        _merge_if_present(payload, engine, "isready_sync_strategy")

        config_path = config_dir / f"{name}.yaml"
        config_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")
        engine["engine_path"] = str(config_path)


def write_temp_config(payload: ConfigPayload, *, label: str) -> Path:
    output_dir = _resolve_output_dir(payload.get("output_dir"))
    config_dir = output_dir / "cli" / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / f"{label}-{timestamp_slug()}.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")
    return path


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
