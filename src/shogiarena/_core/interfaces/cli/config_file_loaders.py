"""CLI-side boundary parsers for configuration payloads."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TypeVar

import yaml
from omegaconf import OmegaConf
from pydantic import BaseModel, ConfigDict

from shogiarena._core.shared.kernel.contracts import parse_wire

_BOUNDARY_ID_CLI_TOURNAMENT_RUN_CONFIG = "BND-CLI-TOURNAMENT-RUN-CONFIG"
_BOUNDARY_ID_CLI_SPSA_RUN_CONFIG = "BND-CLI-SPSA-RUN-CONFIG"
_BOUNDARY_ID_CLI_ENGINE_CONFIG = "BND-CLI-ENGINE-CONFIG"

_WireModelT = TypeVar("_WireModelT", bound=BaseModel)


class _TournamentRunConfigWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_name: str | None = None
    engines: list[dict[str, object]]
    tournament: dict[str, object] | None = None
    generate: dict[str, object] | None = None
    rules: dict[str, object]
    sprt: dict[str, object] | None = None
    openbench: dict[str, object] | None = None
    rating: dict[str, object] | None = None
    dashboard: dict[str, object] | None = None
    log_level: str = "INFO"
    system: dict[str, object] | None = None
    records_output: dict[str, object] | None = None
    instances: str | tuple[object, ...] | list[object] | set[object] | None = None
    output_dir: str | Path | None = None


class _SpsaRunConfigWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_name: str | None = None
    engines: list[dict[str, object]]
    rules: dict[str, object]
    spsa: dict[str, object]
    dashboard: dict[str, object] | None = None
    system: dict[str, object] | None = None
    instances: str | tuple[object, ...] | list[object] | set[object] | None = None


class _EngineConfigWire(BaseModel):
    model_config = ConfigDict(extra="allow")

    engine_path: str | None = None


def _load_yaml_mapping(config_file: str | Path) -> dict[str, object]:
    config_path = Path(config_file).resolve()
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, Mapping):
        raise TypeError(f"Configuration YAML must be a mapping: {config_path}")
    return {str(key): value for key, value in dict(raw).items()}


def _load_omegaconf_mapping(config_file: str | Path) -> dict[str, object]:
    config_path = Path(config_file).resolve()
    data = OmegaConf.to_container(OmegaConf.load(str(config_path)), resolve=True)
    if not isinstance(data, Mapping):
        raise TypeError("SPSA config file must be a mapping")
    return {str(key): value for key, value in dict(data).items()}


def _parse_config_wire_payload(
    *,
    boundary_id: str,
    payload: Mapping[str, object],
    model: type[_WireModelT],
) -> dict[str, object]:
    wire_payload = parse_wire(
        boundary_id=boundary_id,
        payload=payload,
        model=model,
    )
    # Avoid leaking absent optional sections as explicit ``None`` values.
    # Downstream config builders should treat omitted and null-equivalent optional
    # fields consistently.
    return {str(key): value for key, value in wire_payload.model_dump(mode="python", exclude_none=True).items()}


def _validate_rules_initial_positions(payload: Mapping[str, object]) -> None:
    rules_raw = payload.get("rules")
    if not isinstance(rules_raw, Mapping):
        return
    rules_map = {str(key): value for key, value in dict(rules_raw).items()}
    initial_positions_raw = rules_map.get("initial_positions")
    if not isinstance(initial_positions_raw, Mapping):
        return
    initial_positions = {str(key): value for key, value in dict(initial_positions_raw).items()}
    source_type = initial_positions.get("type")
    if source_type != "file":
        return
    source = initial_positions.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("rules.initial_positions.source is required")


def parse_tournament_config_boundary(
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Validate and parse tournament configuration payload."""
    parsed_payload = _parse_config_wire_payload(
        boundary_id=_BOUNDARY_ID_CLI_TOURNAMENT_RUN_CONFIG,
        payload=payload,
        model=_TournamentRunConfigWire,
    )
    return parsed_payload


def parse_spsa_config_boundary(
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Validate and parse SPSA configuration payload."""
    parsed_payload = _parse_config_wire_payload(
        boundary_id=_BOUNDARY_ID_CLI_SPSA_RUN_CONFIG,
        payload=payload,
        model=_SpsaRunConfigWire,
    )
    _validate_rules_initial_positions(parsed_payload)
    return parsed_payload


def parse_tournament_config_file(config_file: str | Path) -> dict[str, object]:
    """Load and parse a tournament configuration YAML file."""
    config_path = Path(config_file).resolve()
    payload = _load_yaml_mapping(config_path)
    return parse_tournament_config_boundary(payload)


def parse_spsa_config_file(config_file: str | Path) -> dict[str, object]:
    """Load and parse an SPSA configuration YAML file."""
    config_path = Path(config_file).resolve()
    payload = _load_omegaconf_mapping(config_path)
    return parse_spsa_config_boundary(payload)


def parse_engine_config_file(config_file: str | Path) -> dict[str, object]:
    """Load and parse an engine configuration YAML file."""
    config_path = Path(config_file).resolve()
    payload = _load_yaml_mapping(config_path)
    parsed_payload = _parse_config_wire_payload(
        boundary_id=_BOUNDARY_ID_CLI_ENGINE_CONFIG,
        payload=payload,
        model=_EngineConfigWire,
    )
    return parsed_payload


__all__ = [
    "parse_engine_config_file",
    "parse_spsa_config_boundary",
    "parse_spsa_config_file",
    "parse_tournament_config_boundary",
    "parse_tournament_config_file",
]
