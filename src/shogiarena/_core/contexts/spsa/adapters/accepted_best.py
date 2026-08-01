"""Runtime-owned persistence for the accepted SPSA promotion artifact."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.content_hashing import sha256_file
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonScalar
from shogiarena._core.shared.kernel.run_artifact_hashes import canonical_sha256
from shogiarena._core.shared.kernel.serialization import json_serialize

ACCEPTED_BEST_SCHEMA = "shogiarena.spsa.accepted_best.v1"
ACCEPTED_BEST_RELATIVE_PATH = Path("spsa") / "accepted-best.json"


def persist_accepted_best(
    *,
    run_dir: Path,
    ledger_commit: Mapping[str, object],
    parameter_wire_values: Mapping[str, JsonScalar],
    baseline_engine_count: int,
    tuned_engine_count: int,
) -> Path:
    """Persist one deterministic artifact from a durable accepted ledger commit."""

    if baseline_engine_count < 1 or tuned_engine_count < 1:
        raise ValueError("accepted-best requires baseline and tuned engine identities")
    run_id = _required_text(ledger_commit.get("run_id"), field="ledger_commit.run_id")
    update_idx = _required_int(ledger_commit.get("update_idx"), field="ledger_commit.update_idx")
    if update_idx < 1:
        raise ValueError("accepted-best update_idx must be positive")
    commit_id = _required_sha256(ledger_commit.get("commit_id"), field="ledger_commit.commit_id")
    created_at = _required_text(ledger_commit.get("created_at"), field="ledger_commit.created_at")
    acceptance = _required_text(ledger_commit.get("acceptance"), field="ledger_commit.acceptance")
    if acceptance not in {"without_ltc", "ltc_pass"}:
        raise ValueError(f"accepted-best acceptance is invalid: {acceptance}")

    parameters = _parameter_payloads(
        ledger_commit.get("parameters"),
        parameter_wire_values=parameter_wire_values,
    )
    manifest_path = run_dir / "manifest.json"
    manifest = _read_json_object(manifest_path, label="sealed run manifest")
    if manifest.get("status") != "provenance_sealed":
        raise ValueError("accepted-best requires a provenance_sealed manifest.json")
    hashes = _required_object(manifest.get("hashes"), field="manifest.hashes")
    resume_hash = _required_sha256(hashes.get("resume_hash"), field="manifest.hashes.resume_hash")
    handshake_digest, tunable_manifest_digest = _tunable_digests(run_dir=run_dir, manifest=manifest)
    engines = _engine_identities(manifest.get("engines"))
    expected_engine_count = baseline_engine_count + tuned_engine_count
    if len(engines) != expected_engine_count:
        raise ValueError("accepted-best engine identity count does not match the sealed baseline/tuned configuration")
    ltc_decision = ledger_commit.get("ltc_decision")
    if acceptance == "ltc_pass" and not isinstance(ltc_decision, Mapping):
        raise ValueError("accepted-best LTC acceptance requires a durable LTC decision identity")
    if acceptance == "without_ltc" and ltc_decision is not None:
        raise ValueError("accepted-best non-LTC acceptance must not carry an LTC decision identity")

    payload: JsonObject = {
        "schema_version": ACCEPTED_BEST_SCHEMA,
        "run_id": run_id,
        "update_idx": update_idx,
        "created_at": created_at,
        "ledger": {
            "commit_id": commit_id,
            "update_revision": _required_int(
                ledger_commit.get("update_revision"),
                field="ledger_commit.update_revision",
            ),
        },
        "parameters": parameters,
        "acceptance": {
            "kind": acceptance,
            "ltc_decision": json_serialize(ltc_decision) if ltc_decision is not None else None,
        },
        "provenance": {
            "run_manifest_sha256": sha256_file(manifest_path),
            "resume_hash": resume_hash,
            "tunable_handshake_sha256": handshake_digest,
            "tunable_manifest_sha256": tunable_manifest_digest,
            "baseline_engines": engines[:baseline_engine_count],
            "tuned_engines": engines[baseline_engine_count:],
        },
    }
    target = run_dir / ACCEPTED_BEST_RELATIVE_PATH
    write_json_atomic(target, payload)
    return target


def _parameter_payloads(
    raw: object,
    *,
    parameter_wire_values: Mapping[str, JsonScalar],
) -> list[JsonObject]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("accepted-best ledger parameters must be a non-empty list")
    parameters: list[JsonObject] = []
    used_options: set[str] = set()
    for index, item in enumerate(raw):
        record = _required_object(item, field=f"ledger_commit.parameters[{index}]")
        parameter_id = _required_text(record.get("parameter_id"), field="parameter_id")
        option_name = _required_text(record.get("option_name"), field="option_name")
        value = record.get("value")
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise ValueError(f"accepted-best parameter value is invalid: {parameter_id}")
        if option_name not in parameter_wire_values:
            raise ValueError(f"accepted-best wire value is missing: {option_name}")
        wire_value = parameter_wire_values[option_name]
        if wire_value is None or isinstance(wire_value, bool):
            raise ValueError(f"accepted-best wire value is invalid: {option_name}")
        used_options.add(option_name)
        parameters.append(
            {
                "parameter_id": parameter_id,
                "option_name": option_name,
                "value": value,
                "wire_value": wire_value,
            }
        )
    extras = set(parameter_wire_values) - used_options
    if extras:
        raise ValueError(f"accepted-best has unknown wire values: {sorted(extras)}")
    return parameters


def _tunable_digests(*, run_dir: Path, manifest: JsonObject) -> tuple[str, str | None]:
    inputs = _required_object(manifest.get("inputs"), field="manifest.inputs")
    entry = _required_object(
        inputs.get("spsa_tunable_handshake"),
        field="manifest.inputs.spsa_tunable_handshake",
    )
    if entry.get("path") != "spsa/tunable_handshake.json":
        raise ValueError("accepted-best tunable handshake path is invalid")
    handshake_digest = _required_sha256(entry.get("sha256"), field="tunable handshake sha256")
    handshake = _read_json_object(run_dir / "spsa" / "tunable_handshake.json", label="tunable handshake")
    if canonical_sha256(handshake) != handshake_digest:
        raise ValueError("accepted-best tunable handshake digest does not match the sealed manifest")
    tunable_manifest = handshake.get("manifest")
    return (
        handshake_digest,
        canonical_sha256(tunable_manifest) if isinstance(tunable_manifest, Mapping) else None,
    )


def _engine_identities(raw: object) -> list[JsonObject]:
    if not isinstance(raw, list):
        raise ValueError("accepted-best sealed engine identities must be a list")
    identities: list[JsonObject] = []
    for index, item in enumerate(raw):
        engine = _required_object(item, field=f"manifest.engines[{index}]")
        bytes_hash = _required_object(engine.get("bytes_hash"), field=f"manifest.engines[{index}].bytes_hash")
        binary_digest = _required_sha256(
            bytes_hash.get("engine_binary_sha256"),
            field=f"manifest.engines[{index}].bytes_hash.engine_binary_sha256",
        )
        identities.append(
            {
                "name": engine.get("name") if isinstance(engine.get("name"), str) else None,
                "artifact": engine.get("artifact") if isinstance(engine.get("artifact"), str) else None,
                "engine_binary_sha256": binary_digest,
                "engine_config_sha256": bytes_hash.get("engine_config_sha256"),
                "path_options": json_serialize(bytes_hash.get("path_options")),
            }
        )
    return identities


def _read_json_object(path: Path, *, label: str) -> JsonObject:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"accepted-best {label} is missing or invalid: {path}") from exc
    return _required_object(raw, field=label)


def _required_object(raw: object, *, field: str) -> JsonObject:
    if not isinstance(raw, Mapping):
        raise ValueError(f"accepted-best {field} must be an object")
    return {str(key): json_serialize(value) for key, value in raw.items()}


def _required_text(raw: object, *, field: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"accepted-best {field} must be a non-empty string")
    return raw


def _required_int(raw: object, *, field: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ValueError(f"accepted-best {field} must be an integer")
    return raw


def _required_sha256(raw: object, *, field: str) -> str:
    value = _required_text(raw, field=field)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"accepted-best {field} must be a lowercase SHA-256 digest")
    return value


__all__ = [
    "ACCEPTED_BEST_RELATIVE_PATH",
    "ACCEPTED_BEST_SCHEMA",
    "persist_accepted_best",
]
