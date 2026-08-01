"""Engine-origin SPSA tunable manifest preflight."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from shogiarena._core.contexts.game_session.adapters.orchestration.config_spsa_models import SpsaRunConfig
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.spsa.application.space_spec import (
    SpsaSpaceSpec,
    inspect_spsa_manifest_request,
    load_spsa_space_spec,
    parse_spsa_tunable_manifest,
    persist_normalized_space,
    validate_space_against_manifest,
)
from shogiarena._core.platform.engine_runtime.usi_protocol_types import UsiOption
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonObject

TUNABLE_HANDSHAKE_SCHEMA = "shogiarena.spsa.tunable-handshake.v1"
TUNABLE_HANDSHAKE_FILENAME = "tunable_handshake.json"


@runtime_checkable
class _TunableEngine(Protocol):
    async def start(self) -> None: ...

    def get_usi_options(self) -> Mapping[str, UsiOption]: ...

    async def request_tunable_manifest(
        self,
        *,
        command: str = "usi_tunables",
        timeout: float | None = None,
    ) -> JsonObject | None: ...

    async def close(self) -> None: ...


async def run_tunable_manifest_preflight(
    *,
    config: SpsaRunConfig,
    run_dir: Path,
    engine_factory_service: EngineFactoryService,
    instance_pool: InstancePool,
) -> tuple[SpsaSpaceSpec, JsonObject]:
    """Handshake one canonical tuned runtime and seal its normalized evidence."""

    request = inspect_spsa_manifest_request(config.space_path)
    engine_config = config.tuned[0]
    config_path = engine_config.engine_path
    if config_path is None:
        raise ValueError("SPSA tunable manifest preflight requires a resolved tuned engine config")
    timeout = float(engine_config.handshake_timeout or config.system.engine_handshake_timeout or 120.0)
    engine = await engine_factory_service.create_engine(
        Path(config_path),
        timeout=timeout,
        engine_name=str(engine_config.name or "tuned-preflight"),
        instance_id=engine_config.instance_id,
        instance_pool=instance_pool,
        cpu_affinity=engine_config.cpu_affinity,
        option_validation="strict",
    )
    if not isinstance(engine, _TunableEngine):
        await _close_if_supported(engine)
        raise TypeError("engine runtime does not support SPSA tunable manifest preflight")
    manifest_payload: JsonObject | None = None
    response_status = "received"
    try:
        await engine.start()
        try:
            manifest_payload = await engine.request_tunable_manifest(
                command=request.command,
                timeout=timeout,
            )
        except TimeoutError:
            if request.required or request.has_selection or not request.has_explicit_parameters:
                raise
            response_status = "timeout_optional"
        advertised = dict(engine.get_usi_options())
    finally:
        await engine.close()

    if manifest_payload is None and (request.required or request.has_selection or not request.has_explicit_parameters):
        raise ValueError("SPSA tunable manifest is required but the engine returned no manifest payload")
    manifest = parse_spsa_tunable_manifest(manifest_payload) if manifest_payload is not None else None
    space = load_spsa_space_spec(config.space_path, manifest=manifest)
    if manifest is not None:
        validate_space_against_manifest(space, manifest)
    _validate_selected_options(space=space, advertised=advertised)
    _validate_clear_hash_option(
        required=config.variants.apply.is_clear_hash_enabled,
        advertised=advertised,
    )
    evidence: JsonObject = {
        "schema_version": TUNABLE_HANDSHAKE_SCHEMA,
        "status": "passed",
        "response_status": response_status if manifest_payload is None else "received",
        "command": request.command,
        "required": request.required,
        "runtime_scope": "remote_runtime" if engine_config.instance_id else "local_runtime",
        "instance_id": engine_config.instance_id,
        "engine_name": engine_config.name,
        "manifest": manifest_payload,
        "advertised_options": [_option_payload(option) for _, option in sorted(advertised.items())],
        "normalized_space": space.to_json(),
    }
    persist_normalized_space(run_dir, space)
    write_json_atomic(run_dir / "spsa" / TUNABLE_HANDSHAKE_FILENAME, evidence)
    return space, evidence


def validate_sealed_tunable_evidence(
    *,
    space_path: str | Path,
    normalized_space_path: str | Path,
    evidence: Mapping[str, object],
    clear_hash_required: bool,
) -> tuple[SpsaSpaceSpec, JsonObject]:
    """Rebuild and validate normalized space from sealed handshake evidence."""

    payload = coerce_json_object_serialized(evidence, field_name="tunable_handshake")
    if payload.get("schema_version") != TUNABLE_HANDSHAKE_SCHEMA or payload.get("status") != "passed":
        raise ValueError("SPSA sealed tunable handshake evidence is invalid")
    raw_manifest = payload.get("manifest")
    manifest = parse_spsa_tunable_manifest(raw_manifest) if isinstance(raw_manifest, dict) else None
    space = load_spsa_space_spec(space_path, manifest=manifest)
    if manifest is not None:
        validate_space_against_manifest(space, manifest)
    advertised = _options_from_evidence(payload.get("advertised_options"))
    _validate_selected_options(space=space, advertised=advertised)
    _validate_clear_hash_option(required=clear_hash_required, advertised=advertised)
    if payload.get("normalized_space") != space.to_json():
        raise ValueError("SPSA sealed tunable handshake normalized space mismatch")
    try:
        persisted_normalized = json.loads(Path(normalized_space_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("SPSA sealed normalized space artifact is missing or invalid") from exc
    if persisted_normalized != space.to_json():
        raise ValueError("SPSA sealed normalized space artifact mismatch")
    return space, payload


def _validate_selected_options(*, space: SpsaSpaceSpec, advertised: Mapping[str, UsiOption]) -> None:
    for parameter in space.parameters:
        option = advertised.get(parameter.option)
        if option is None:
            raise ValueError(f"SPSA selected option is not advertised by engine: {parameter.option}")
        if parameter.value_type == "int":
            if option.option_type != "spin":
                raise ValueError(f"SPSA int tunable must be advertised as spin: {parameter.option}")
            if option.minimum is None or option.maximum is None:
                raise ValueError(f"SPSA spin tunable has no advertised bounds: {parameter.option}")
            if parameter.minimum < option.minimum or parameter.maximum > option.maximum:
                raise ValueError(f"SPSA bounds exceed advertised spin bounds: {parameter.option}")
        elif option.option_type != "string":
            raise ValueError(f"SPSA float tunable must be advertised as string: {parameter.option}")


def _validate_clear_hash_option(*, required: bool, advertised: Mapping[str, UsiOption]) -> None:
    if not required:
        return
    option = advertised.get("Clear Hash")
    if option is None or option.option_type != "button":
        raise ValueError(
            "SPSA variants.apply.clear_hash=true requires the tuned engine to advertise 'Clear Hash' as a USI button"
        )


def _option_payload(option: UsiOption) -> JsonObject:
    return {
        "name": option.name,
        "type": option.option_type,
        "default": option.default,
        "minimum": option.minimum,
        "maximum": option.maximum,
        "choices": list(option.choices),
    }


def _options_from_evidence(raw: object) -> dict[str, UsiOption]:
    if not isinstance(raw, list):
        raise TypeError("SPSA sealed tunable handshake advertised_options must be a list")
    options: dict[str, UsiOption] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TypeError(f"SPSA advertised_options[{index}] must be an object")
        option_payload = coerce_json_object_serialized(item, field_name=f"advertised_options[{index}]")
        name = option_payload.get("name")
        option_type = option_payload.get("type")
        if not isinstance(name, str) or not isinstance(option_type, str):
            raise TypeError(f"SPSA advertised_options[{index}] identity is invalid")
        if name in options:
            raise ValueError(f"duplicate advertised USI option: {name}")
        default = option_payload.get("default")
        minimum = option_payload.get("minimum")
        maximum = option_payload.get("maximum")
        raw_choices = option_payload.get("choices")
        choices = raw_choices if isinstance(raw_choices, list) else []
        options[name] = UsiOption(
            name=name,
            option_type=option_type,
            default=default if isinstance(default, str) else None,
            minimum=minimum if isinstance(minimum, int) else None,
            maximum=maximum if isinstance(maximum, int) else None,
            choices=tuple(str(value) for value in choices),
        )
    return options


async def _close_if_supported(engine: Any) -> None:
    close = getattr(engine, "close", None)
    if callable(close):
        await close()


__all__ = [
    "TUNABLE_HANDSHAKE_FILENAME",
    "TUNABLE_HANDSHAKE_SCHEMA",
    "run_tunable_manifest_preflight",
    "validate_sealed_tunable_evidence",
]
