"""Helpers for building config payloads from CLI inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from shogiarena.cli.errors import CliArgumentError
from shogiarena.utils.common import project_dirs
from shogiarena.utils.common.paths import resolve_path_like
from shogiarena.utils.common.run_paths import timestamp_slug

from .config_overrides import apply_override, apply_section_overrides, parse_scalar


def build_cli_config_payload(
    *,
    base: dict[str, Any] | None,
    engines_tokens: list[list[str]] | None,
    sections: dict[str, list[str] | None],
    experiment_name: str | None,
    default_experiment: str,
    label: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = dict(base or {})
    if experiment_name:
        payload["experiment_name"] = experiment_name
    elif "experiment_name" not in payload:
        payload["experiment_name"] = default_experiment

    output_dir = _resolve_output_dir(payload.get("output_dir"))

    if engines_tokens:
        engines = [parse_engine_tokens(tokens) for tokens in engines_tokens]
        _ensure_engine_names(engines)
        _normalize_engine_overlays(engines)
        materialize_engine_configs(engines, label=label, output_dir=output_dir)
        payload["engines"] = engines

    for section, tokens in sections.items():
        if tokens:
            apply_section_overrides(payload, section, tokens)

    _normalize_rules_paths(payload)
    return payload


def parse_engine_tokens(tokens: list[str]) -> dict[str, Any]:
    if not tokens:
        raise CliArgumentError("engine tokens are empty")
    engine: dict[str, Any] = {}
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
    engines: list[dict[str, Any]],
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
        if isinstance(engine.get("engine_config"), str) and str(engine["engine_config"]).strip():
            engine["engine_config"] = str(Path(resolve_path_like(str(engine["engine_config"]))).resolve())
            continue

        engine_path = engine.pop("engine_path", None) or engine.pop("path", None) or engine.pop("binary", None)
        if engine_path is None:
            raise CliArgumentError("engine must specify artifact, engine_config, or engine_path")

        resolved_path = Path(resolve_path_like(str(engine_path))).resolve()
        if not resolved_path.exists():
            raise CliArgumentError(f"engine_path not found: {resolved_path}")
        if not resolved_path.is_file():
            raise CliArgumentError(f"engine_path is not a file: {resolved_path}")

        name = str(engine.get("name") or f"engine-{idx}")
        payload: dict[str, Any] = {
            "name": name,
            "engine_path": str(resolved_path),
            "working_directory": str(Path(engine.pop("working_directory", resolved_path.parent)).resolve()),
        }
        _merge_if_present(payload, engine, "engine_args")
        _merge_if_present(payload, engine, "environment")
        _merge_if_present(payload, engine, "go_options")
        _merge_if_present(payload, engine, "enable_early_ponder")

        config_path = config_dir / f"{name}.yaml"
        config_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")
        engine["engine_config"] = str(config_path)


def write_temp_config(payload: dict[str, Any], *, label: str) -> Path:
    output_dir = _resolve_output_dir(payload.get("output_dir"))
    config_dir = output_dir / "cli" / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / f"{label}-{timestamp_slug()}.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")
    return path


def _resolve_output_dir(raw: Any | None) -> Path:
    if raw is None:
        return project_dirs.output_dir
    return Path(resolve_path_like(str(raw)))


def _merge_if_present(payload: dict[str, Any], engine: dict[str, Any], key: str) -> None:
    if key in engine:
        payload[key] = engine.pop(key)


def _append_list(target: dict[str, Any], key: str, value: Any) -> None:
    if key not in target or target[key] is None:
        target[key] = []
    current = target[key]
    if not isinstance(current, list):
        raise CliArgumentError(f"{key} must be a list")
    if isinstance(value, list):
        current.extend(value)
    else:
        current.append(value)


def _ensure_engine_names(engines: list[dict[str, Any]]) -> None:
    for idx, engine in enumerate(engines, 1):
        if not engine.get("name"):
            engine["name"] = f"engine-{idx}"


def _normalize_engine_overlays(engines: list[dict[str, Any]]) -> None:
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


def _normalize_rules_paths(payload: dict[str, Any]) -> None:
    rules = payload.get("rules")
    if not isinstance(rules, dict):
        return
    initial_positions = rules.get("initial_positions")
    if not isinstance(initial_positions, dict):
        return
    source = initial_positions.get("source")
    if isinstance(source, str) and source.strip():
        initial_positions["source"] = str(Path(resolve_path_like(source)).resolve())
        rules["initial_positions"] = initial_positions
        payload["rules"] = rules


def resolve_run_dir(experiment_name: str, output_dir: Path, run_dir_override: str | None) -> Path:
    if run_dir_override:
        return Path(resolve_path_like(run_dir_override))
    return output_dir / "tournament" / experiment_name / timestamp_slug()


def prepare_positions_file(
    *,
    run_dir: Path,
    position: str,
    positions_file: str | None,
    filename: str,
) -> Path | None:
    if positions_file:
        path = Path(resolve_path_like(positions_file))
        if not path.exists():
            raise CliArgumentError(f"positions file not found: {path}")
        lines = path.read_text(encoding="utf-8").splitlines()
        filtered = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
        if not filtered:
            raise CliArgumentError("positions file did not contain any usable lines")
        return _write_positions(run_dir, filename=filename, positions=filtered)

    if position.strip() and position.strip() != "startpos":
        return _write_positions(run_dir, filename=filename, positions=[position.strip()])
    return None


def _write_positions(run_dir: Path, *, filename: str, positions: list[str]) -> Path:
    positions_dir = run_dir / "cli" / "positions"
    positions_dir.mkdir(parents=True, exist_ok=True)
    path = positions_dir / filename
    path.write_text("\n".join(positions) + "\n", encoding="utf-8")
    return path
