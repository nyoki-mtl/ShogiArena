"""SPSA parameter-space parsing and normalization."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from omegaconf import DictConfig, OmegaConf

from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_float, coerce_int, coerce_str

SPACE_SCHEMA_VERSION = "shogiarena.spsa.space.v1"

ValueType = Literal["int", "float"]
ValueEncoding = Literal["integer", "decimal", "scaled_integer"]
RoundingMode = Literal["none", "nearest", "stochastic"]


@dataclass(frozen=True, slots=True)
class SpsaTunableDescriptor:
    """Engine-origin tunable descriptor."""

    id: str
    option: str
    value_type: ValueType
    value_encoding: ValueEncoding
    default: float | None
    minimum: float | None
    maximum: float | None
    c_end: float | None
    r_end: float | None
    scale: float | None = None
    significant_digits: int = 9


@dataclass(frozen=True, slots=True)
class SpsaTunableManifest:
    """Engine-origin tunable manifest."""

    schema_version: str
    tunables: tuple[SpsaTunableDescriptor, ...]

    def by_id(self) -> dict[str, SpsaTunableDescriptor]:
        """Return descriptors keyed by stable tunable id."""
        return {descriptor.id: descriptor for descriptor in self.tunables}


@dataclass(frozen=True, slots=True)
class SpsaManifestRequest:
    """Manifest transport requirements declared by a space source."""

    required: bool
    command: str
    has_explicit_parameters: bool
    has_selection: bool


@dataclass(frozen=True, slots=True)
class SpsaParameterSpec:
    """Normalized immutable SPSA parameter spec."""

    id: str
    option: str
    value_type: ValueType
    initial: float
    minimum: float
    maximum: float
    c_end: float
    r_end: float
    value_encoding: ValueEncoding
    rounding: RoundingMode = "none"
    label: str | None = None
    description: str | None = None
    scale: float | None = None
    significant_digits: int = 9

    def to_param_entry(self) -> ParamEntry:
        """Convert to the runtime parameter representation."""
        return ParamEntry(
            name=self.id,
            type=self.value_type,
            value=self.initial,
            min=self.minimum,
            max=self.maximum,
            step=self.c_end,
            delta=self.r_end,
            comment=self.description or "",
            is_not_used=False,
            option_name=self.option,
            value_encoding=self.value_encoding,
            scale=self.scale,
            significant_digits=self.significant_digits,
            rounding=self.rounding,
        )

    def to_json(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        payload: JsonObject = {
            "id": self.id,
            "target": {
                "option": self.option,
                "value_encoding": self.value_encoding,
            },
            "value_type": self.value_type,
            "initial": self.initial,
            "bounds": {"min": self.minimum, "max": self.maximum},
            "schedule": {"c_end": self.c_end, "r_end": self.r_end},
            "rounding": {"mode": self.rounding},
            "significant_digits": self.significant_digits,
        }
        if self.label:
            payload["label"] = self.label
        if self.description:
            payload["description"] = self.description
        if self.scale is not None:
            target = payload["target"]
            if isinstance(target, dict):
                target["scale"] = self.scale
        return payload


@dataclass(frozen=True, slots=True)
class SpsaSpaceSpec:
    """Normalized SPSA parameter space."""

    schema_version: str
    engine_family: str | None
    protocol: str
    parameters: tuple[SpsaParameterSpec, ...]
    manifest_required: bool = False
    manifest_command: str = "usi_tunables"

    def to_json(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "target": {
                "engine_family": self.engine_family,
                "protocol": self.protocol,
                "required_options_policy": "strict",
                "tunable_manifest": {
                    "required": self.manifest_required,
                    "command": self.manifest_command,
                },
            },
            "parameters": [param.to_json() for param in self.parameters],
        }

    def to_param_entries(self) -> list[ParamEntry]:
        """Convert all parameters to runtime entries."""
        return [param.to_param_entry() for param in self.parameters]


def load_spsa_space_spec(
    path: str | Path,
    *,
    manifest: SpsaTunableManifest | dict[str, object] | None = None,
) -> SpsaSpaceSpec:
    """Load and normalize an SPSA space spec from YAML or JSON."""
    space_path = Path(path)
    if not space_path.exists():
        raise FileNotFoundError(f"SPSA space file not found: {space_path}")
    payload = _load_space_payload(space_path)
    return parse_spsa_space_spec(payload, source_path=space_path, manifest=manifest)


def inspect_spsa_manifest_request(path: str | Path) -> SpsaManifestRequest:
    """Read only the manifest transport declaration from a space source."""

    payload = _load_space_payload(Path(path))
    target = _mapping(payload.get("target"), field="space.target")
    manifest_node = target.get("tunable_manifest")
    manifest_map = _mapping(manifest_node, field="space.target.tunable_manifest") if manifest_node else {}
    command = coerce_str(manifest_map.get("command")) or "usi_tunables"
    if "\n" in command or "\r" in command:
        raise ValueError("space.target.tunable_manifest.command must be one line")
    return SpsaManifestRequest(
        required=bool(manifest_map.get("required", False)),
        command=command,
        has_explicit_parameters=isinstance(payload.get("parameters"), list),
        has_selection=payload.get("select") is not None,
    )


def parse_spsa_space_spec(
    raw: Mapping[str, object],
    *,
    source_path: Path | None = None,
    manifest: SpsaTunableManifest | dict[str, object] | None = None,
) -> SpsaSpaceSpec:
    """Normalize an SPSA space spec mapping."""
    payload = coerce_json_object_serialized(raw, field_name="space")
    schema_version = coerce_str(payload.get("schema_version"))
    if schema_version != SPACE_SCHEMA_VERSION:
        raise ValueError(f"space.schema_version must be {SPACE_SCHEMA_VERSION!r}")

    target = _mapping(payload.get("target"), field="space.target")
    protocol = coerce_str(target.get("protocol")) or "usi_options"
    if protocol != "usi_options":
        raise ValueError("space.target.protocol must be 'usi_options'")
    engine_family = coerce_str(target.get("engine_family"))
    manifest_node = target.get("tunable_manifest")
    manifest_required = False
    manifest_command = "usi_tunables"
    if isinstance(manifest_node, dict):
        manifest_required = bool(manifest_node.get("required", False))
        manifest_command = coerce_str(manifest_node.get("command")) or "usi_tunables"

    raw_parameters = payload.get("parameters")
    if raw_parameters is None and payload.get("select") is not None:
        raw_parameters = _resolve_manifest_parameters(payload, manifest=manifest)
    if not isinstance(raw_parameters, list) or not raw_parameters:
        where = f" in {source_path}" if source_path is not None else ""
        raise ValueError(f"space.parameters must be a non-empty list{where}")

    parameters: list[SpsaParameterSpec] = []
    seen_ids: set[str] = set()
    seen_options: set[str] = set()
    for index, raw_param in enumerate(raw_parameters):
        if not isinstance(raw_param, dict):
            raise TypeError(f"space.parameters[{index}] must be a mapping")
        spec = _parse_parameter(raw_param, index=index)
        if spec.id in seen_ids:
            raise ValueError(f"duplicate SPSA parameter id: {spec.id}")
        if spec.option in seen_options:
            raise ValueError(f"duplicate SPSA target option: {spec.option}")
        seen_ids.add(spec.id)
        seen_options.add(spec.option)
        parameters.append(spec)

    return SpsaSpaceSpec(
        schema_version=schema_version,
        engine_family=engine_family,
        protocol=protocol,
        parameters=tuple(parameters),
        manifest_required=manifest_required,
        manifest_command=manifest_command,
    )


def parse_spsa_tunable_manifest(raw: Mapping[str, object]) -> SpsaTunableManifest:
    """Normalize an engine-origin tunable manifest."""
    payload = coerce_json_object_serialized(raw, field_name="manifest")
    schema_version = coerce_str(payload.get("schema_version"))
    if schema_version != "shogiarena.usi_tunables.v1":
        raise ValueError("manifest.schema_version must be 'shogiarena.usi_tunables.v1'")
    raw_tunables = payload.get("tunables")
    if not isinstance(raw_tunables, list) or not raw_tunables:
        raise ValueError("manifest.tunables must be a non-empty list")
    tunables: list[SpsaTunableDescriptor] = []
    seen_ids: set[str] = set()
    seen_options: set[str] = set()
    for index, raw_tunable in enumerate(raw_tunables):
        if not isinstance(raw_tunable, dict):
            raise TypeError(f"manifest.tunables[{index}] must be a mapping")
        descriptor = _parse_manifest_descriptor(raw_tunable, index=index)
        if descriptor.id in seen_ids:
            raise ValueError(f"duplicate manifest tunable id: {descriptor.id}")
        if descriptor.option in seen_options:
            raise ValueError(f"duplicate manifest tunable option: {descriptor.option}")
        seen_ids.add(descriptor.id)
        seen_options.add(descriptor.option)
        tunables.append(descriptor)
    return SpsaTunableManifest(schema_version=schema_version, tunables=tuple(tunables))


def persist_normalized_space(run_dir: Path, space: SpsaSpaceSpec) -> Path:
    """Persist the immutable normalized space snapshot under ``run_dir``."""
    out_path = run_dir / "spsa" / "space.normalized.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out_path, space.to_json())
    return out_path


def validate_space_against_manifest(
    space: SpsaSpaceSpec,
    manifest: SpsaTunableManifest,
) -> None:
    """Validate that every normalized parameter remains within the engine manifest contract."""

    descriptors = manifest.by_id()
    for parameter in space.parameters:
        descriptor = descriptors.get(parameter.id)
        if descriptor is None:
            raise ValueError(f"SPSA parameter is not in tunable manifest: {parameter.id}")
        if parameter.option != descriptor.option:
            raise ValueError(f"SPSA parameter option conflicts with tunable manifest: {parameter.id}")
        if parameter.value_type != descriptor.value_type:
            raise ValueError(f"SPSA parameter value_type conflicts with tunable manifest: {parameter.id}")
        if parameter.value_encoding != descriptor.value_encoding:
            raise ValueError(f"SPSA parameter encoding conflicts with tunable manifest: {parameter.id}")
        if parameter.scale != descriptor.scale:
            raise ValueError(f"SPSA parameter scale conflicts with tunable manifest: {parameter.id}")
        if descriptor.minimum is None or descriptor.maximum is None:
            raise ValueError(f"SPSA tunable manifest has no bounds: {parameter.id}")
        if parameter.minimum < descriptor.minimum or parameter.maximum > descriptor.maximum:
            raise ValueError(f"SPSA parameter bounds exceed tunable manifest bounds: {parameter.id}")


def _resolve_manifest_parameters(
    payload: JsonObject,
    *,
    manifest: SpsaTunableManifest | dict[str, object] | None,
) -> list[JsonObject]:
    if manifest is None:
        raise ValueError("space.select requires a tunable manifest")
    manifest_obj = parse_spsa_tunable_manifest(manifest) if isinstance(manifest, dict) else manifest
    descriptors = manifest_obj.by_id()
    raw_select = payload.get("select")
    if not isinstance(raw_select, list) or not raw_select:
        raise ValueError("space.select must be a non-empty list")
    overrides = payload.get("overrides")
    override_map = _mapping(overrides, field="space.overrides") if overrides is not None else {}
    parameters: list[JsonObject] = []
    for index, raw_item in enumerate(raw_select):
        if isinstance(raw_item, str):
            selected_id = raw_item
        elif isinstance(raw_item, dict):
            selected_id = _required_str(raw_item.get("id"), field=f"space.select[{index}].id")
        else:
            raise TypeError(f"space.select[{index}] must be a string or mapping")
        descriptor = descriptors.get(selected_id)
        if descriptor is None:
            raise ValueError(f"selected tunable is not in manifest: {selected_id}")
        override = override_map.get(selected_id)
        if override is not None and not isinstance(override, dict):
            raise TypeError(f"space.overrides.{selected_id} must be a mapping")
        parameters.append(_descriptor_to_parameter(descriptor, override=dict(override or {})))
    return parameters


def _descriptor_to_parameter(descriptor: SpsaTunableDescriptor, *, override: JsonObject) -> JsonObject:
    initial = override.get("initial", descriptor.default)
    override_bounds = override.get("bounds")
    bounds = _mapping(override_bounds, field=f"space.overrides.{descriptor.id}.bounds") if override_bounds else {}
    override_schedule = override.get("schedule")
    schedule = (
        _mapping(override_schedule, field=f"space.overrides.{descriptor.id}.schedule") if override_schedule else {}
    )
    return {
        "id": descriptor.id,
        "target": {
            "option": descriptor.option,
            "value_encoding": descriptor.value_encoding,
            **({"scale": descriptor.scale} if descriptor.scale is not None else {}),
        },
        "value_type": descriptor.value_type,
        "initial": initial,
        "bounds": {
            "min": bounds.get("min", descriptor.minimum),
            "max": bounds.get("max", descriptor.maximum),
        },
        "schedule": {
            "c_end": schedule.get("c_end", descriptor.c_end),
            "r_end": schedule.get("r_end", descriptor.r_end),
        },
        **({"rounding": override["rounding"]} if "rounding" in override else {}),
        "significant_digits": override.get("significant_digits", descriptor.significant_digits),
    }


def _parse_manifest_descriptor(raw_tunable: dict[str, JsonValue], *, index: int) -> SpsaTunableDescriptor:
    tunable = coerce_json_object_serialized(raw_tunable, field_name=f"manifest.tunables[{index}]")
    tunable_id = _required_str(tunable.get("id"), field=f"manifest.tunables[{index}].id")
    option = _required_str(tunable.get("option"), field=f"manifest.tunables[{index}].option")
    value_type = _parse_manifest_value_type(tunable.get("value_type"), index=index)
    encoding = _parse_manifest_encoding(tunable.get("encoding"), value_type=value_type, index=index)
    schedule_node = tunable.get("schedule")
    schedule = _mapping(schedule_node, field=f"manifest.tunables[{index}].schedule") if schedule_node else {}
    scale = coerce_float(tunable.get("scale"))
    if encoding == "scaled_integer" and (scale is None or scale <= 0):
        raise ValueError(f"manifest.tunables[{index}].scale must be positive for scaled_integer")
    significant_digits = _positive_int(
        tunable.get("significant_digits"),
        field=f"manifest.tunables[{index}].significant_digits",
        default=9,
    )
    return SpsaTunableDescriptor(
        id=tunable_id,
        option=option,
        value_type=value_type,
        value_encoding=encoding,
        default=coerce_float(tunable.get("default")),
        minimum=coerce_float(tunable.get("min")),
        maximum=coerce_float(tunable.get("max")),
        c_end=coerce_float(schedule.get("c_end")),
        r_end=coerce_float(schedule.get("r_end")),
        scale=scale,
        significant_digits=significant_digits,
    )


def _parse_parameter(raw_param: dict[str, JsonValue], *, index: int) -> SpsaParameterSpec:
    param = coerce_json_object_serialized(raw_param, field_name=f"space.parameters[{index}]")
    param_id = _required_str(param.get("id"), field=f"space.parameters[{index}].id")
    target = _mapping(param.get("target"), field=f"space.parameters[{index}].target")
    option = _required_str(target.get("option"), field=f"space.parameters[{index}].target.option")
    value_type = _parse_value_type(param.get("value_type"), index=index)
    encoding = _parse_encoding(target.get("value_encoding"), value_type=value_type, index=index)
    initial = _finite_float(param.get("initial"), field=f"space.parameters[{index}].initial")
    bounds = _mapping(param.get("bounds"), field=f"space.parameters[{index}].bounds")
    minimum = _finite_float(bounds.get("min"), field=f"space.parameters[{index}].bounds.min")
    maximum = _finite_float(bounds.get("max"), field=f"space.parameters[{index}].bounds.max")
    if minimum >= maximum:
        raise ValueError(f"space.parameters[{index}].bounds.min must be less than bounds.max")
    if not minimum <= initial <= maximum:
        raise ValueError(f"space.parameters[{index}].initial must be within bounds")
    if value_type == "int" and (not float(minimum).is_integer() or not float(maximum).is_integer()):
        raise ValueError(f"space.parameters[{index}].bounds must be integral for an int parameter")
    schedule = _mapping(param.get("schedule"), field=f"space.parameters[{index}].schedule")
    c_end = _finite_float(schedule.get("c_end"), field=f"space.parameters[{index}].schedule.c_end")
    r_end = _finite_float(schedule.get("r_end"), field=f"space.parameters[{index}].schedule.r_end")
    if c_end <= 0:
        raise ValueError(f"space.parameters[{index}].schedule.c_end must be > 0")
    if r_end < 0:
        raise ValueError(f"space.parameters[{index}].schedule.r_end must be >= 0")
    rounding_node = param.get("rounding")
    rounding = "none"
    if isinstance(rounding_node, dict):
        rounding = coerce_str(rounding_node.get("mode")) or "none"
    if rounding not in {"none", "nearest", "stochastic"}:
        raise ValueError(f"space.parameters[{index}].rounding.mode is invalid: {rounding}")
    scale = coerce_float(target.get("scale"))
    if encoding == "scaled_integer" and (scale is None or scale <= 0):
        raise ValueError(f"space.parameters[{index}].target.scale must be positive for scaled_integer")
    if encoding == "scaled_integer" and scale is not None:
        effective_minimum = math.ceil(minimum * scale)
        effective_maximum = math.floor(maximum * scale)
        if effective_minimum > effective_maximum:
            raise ValueError(f"space.parameters[{index}].bounds contain no scaled_integer wire value at scale {scale}")
    significant_digits = _positive_int(
        param.get("significant_digits"),
        field=f"space.parameters[{index}].significant_digits",
        default=9,
    )
    return SpsaParameterSpec(
        id=param_id,
        option=option,
        value_type=value_type,
        initial=initial,
        minimum=minimum,
        maximum=maximum,
        c_end=c_end,
        r_end=r_end,
        value_encoding=encoding,
        rounding=rounding,  # type: ignore[arg-type]
        label=coerce_str(param.get("label")),
        description=coerce_str(param.get("description")),
        scale=scale,
        significant_digits=significant_digits,
    )


def _mapping(value: JsonValue | None, *, field: str) -> JsonObject:
    if not isinstance(value, dict):
        raise TypeError(f"{field} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _load_space_payload(space_path: Path) -> JsonObject:
    if not space_path.exists():
        raise FileNotFoundError(f"SPSA space file not found: {space_path}")
    if space_path.suffix.lower() == ".json":
        raw = json.loads(space_path.read_text(encoding="utf-8"))
    else:
        loaded = OmegaConf.load(space_path)
        raw = OmegaConf.to_container(loaded, resolve=True) if isinstance(loaded, DictConfig) else loaded
    if not isinstance(raw, dict):
        raise TypeError(f"SPSA space spec must be a mapping: {space_path}")
    return coerce_json_object_serialized(raw, field_name="space")


def _required_str(value: JsonValue | None, *, field: str) -> str:
    parsed = coerce_str(value)
    if parsed is None or not parsed.strip():
        raise ValueError(f"{field} is required")
    if any(ord(ch) < 32 for ch in parsed):
        raise ValueError(f"{field} must not contain control characters")
    return parsed.strip()


def _finite_float(value: JsonValue | None, *, field: str) -> float:
    parsed = coerce_float(value)
    if parsed is None or not math.isfinite(parsed):
        raise ValueError(f"{field} must be a finite number")
    return float(parsed)


def _positive_int(value: JsonValue | None, *, field: str, default: int) -> int:
    parsed = default if value is None else coerce_int(value)
    if parsed is None:
        raise ValueError(f"{field} must be an integer")
    if parsed <= 0:
        raise ValueError(f"{field} must be positive")
    return int(parsed)


def _parse_value_type(value: JsonValue | None, *, index: int) -> ValueType:
    parsed = _required_str(value, field=f"space.parameters[{index}].value_type")
    if parsed not in {"int", "float"}:
        raise ValueError(f"space.parameters[{index}].value_type must be 'int' or 'float'")
    return parsed  # type: ignore[return-value]


def _parse_manifest_value_type(value: JsonValue | None, *, index: int) -> ValueType:
    parsed = _required_str(value, field=f"manifest.tunables[{index}].value_type")
    if parsed not in {"int", "float"}:
        raise ValueError(f"manifest.tunables[{index}].value_type must be 'int' or 'float'")
    return parsed  # type: ignore[return-value]


def _parse_encoding(value: JsonValue | None, *, value_type: ValueType, index: int) -> ValueEncoding:
    parsed = coerce_str(value)
    if parsed is None:
        return "integer" if value_type == "int" else "decimal"
    if parsed not in {"integer", "decimal", "scaled_integer"}:
        raise ValueError(f"space.parameters[{index}].target.value_encoding is invalid: {parsed}")
    return parsed  # type: ignore[return-value]


def _parse_manifest_encoding(value: JsonValue | None, *, value_type: ValueType, index: int) -> ValueEncoding:
    parsed = coerce_str(value)
    if parsed is None:
        return "integer" if value_type == "int" else "decimal"
    if parsed not in {"integer", "decimal", "scaled_integer"}:
        raise ValueError(f"manifest.tunables[{index}].encoding is invalid: {parsed}")
    return parsed  # type: ignore[return-value]


__all__ = [
    "SPACE_SCHEMA_VERSION",
    "SpsaParameterSpec",
    "SpsaManifestRequest",
    "SpsaSpaceSpec",
    "SpsaTunableDescriptor",
    "SpsaTunableManifest",
    "load_spsa_space_spec",
    "inspect_spsa_manifest_request",
    "parse_spsa_space_spec",
    "parse_spsa_tunable_manifest",
    "persist_normalized_space",
    "validate_space_against_manifest",
]
