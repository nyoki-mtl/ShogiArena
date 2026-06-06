"""Canonical hash contracts for run artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from shogiarena._core.shared.kernel.hash_normalization import HashInput, normalize_for_hash
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.serialization import json_serialize

RUN_ARTIFACT_HASH_SCHEMA_VERSION = 1
RESUME_CONTRACT_VERSION = 1

_SPRT_TEST_DEFINITION_KEYS = ("elo0", "elo1", "alpha", "beta")


def canonical_json_bytes(value: HashInput) -> bytes:
    """Return deterministic UTF-8 JSON bytes for a hash input."""

    normalized = normalize_for_hash(value)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return encoded.encode("utf-8")


def canonical_sha256(value: HashInput) -> str:
    """Return a SHA-256 hex digest over canonical JSON."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def config_fingerprint(config_payload: Mapping[str, JsonValue]) -> str:
    """Hash the full canonical config payload for integrity checks."""

    return _typed_digest("config_fingerprint", _mapping_to_json_object(config_payload))


def schedule_hash(schedule_payload: Mapping[str, JsonValue]) -> str:
    """Hash the logical run plan used for grouping runs."""

    return _typed_digest("schedule_hash", _mapping_to_json_object(schedule_payload))


def provenance_hash(provenance_payload: Mapping[str, JsonValue]) -> str:
    """Hash the physical bytes/build/eval provenance for resume safety."""

    return _typed_digest("provenance_hash", _mapping_to_json_object(provenance_payload))


def build_sprt_test_definition(sprt_payload: Mapping[str, JsonValue] | None) -> JsonObject | None:
    """Build the resume-only SPRT test definition payload.

    `min_games` and `max_games` are intentionally excluded so an SPRT run can be
    extended or shortened without invalidating resume.
    """

    if sprt_payload is None:
        return None
    normalized = _mapping_to_json_object(sprt_payload)
    definition: JsonObject = {}
    for key in _SPRT_TEST_DEFINITION_KEYS:
        if key in normalized:
            definition[key] = normalized[key]
    return definition or None


def sprt_test_def_hash(sprt_payload: Mapping[str, JsonValue] | None) -> str | None:
    """Hash the SPRT analysis contract used only for resume compatibility."""

    definition = build_sprt_test_definition(sprt_payload)
    if definition is None:
        return None
    return _typed_digest("sprt_test_def_hash", definition)


@dataclass(frozen=True, slots=True)
class ResumeHashRequest:
    """Inputs for building the resume compatibility hash."""

    schedule_hash: str
    provenance_hash: str
    sprt_test_def_hash: str | None = None
    resume_contract_version: int = RESUME_CONTRACT_VERSION

    def to_payload(self) -> JsonObject:
        """Return the canonical resume-hash payload."""

        if not self.schedule_hash:
            raise ValueError("schedule_hash must not be empty")
        if not self.provenance_hash:
            raise ValueError("provenance_hash must not be empty")
        if self.resume_contract_version <= 0:
            raise ValueError("resume_contract_version must be positive")
        return {
            "schema_version": RUN_ARTIFACT_HASH_SCHEMA_VERSION,
            "resume_contract_version": self.resume_contract_version,
            "schedule_hash": self.schedule_hash,
            "provenance_hash": self.provenance_hash,
            "sprt_test_def_hash": self.sprt_test_def_hash,
        }


def resume_hash(request: ResumeHashRequest) -> str:
    """Hash the compatibility contract that decides whether a run is resumable."""

    return _typed_digest("resume_hash", request.to_payload())


@dataclass(frozen=True, slots=True)
class RunArtifactHashBundle:
    """Named hashes recorded in a run manifest."""

    config_fingerprint: str
    schedule_hash: str
    provenance_hash: str | None
    sprt_test_def_hash: str | None
    resume_hash: str | None

    def to_payload(self) -> JsonObject:
        """Return a manifest-ready JSON payload."""

        return {
            "schema_version": RUN_ARTIFACT_HASH_SCHEMA_VERSION,
            "config_fingerprint": self.config_fingerprint,
            "schedule_hash": self.schedule_hash,
            "provenance_hash": self.provenance_hash,
            "sprt_test_def_hash": self.sprt_test_def_hash,
            "resume_hash": self.resume_hash,
        }


@dataclass(frozen=True, slots=True)
class RunArtifactHashRequest:
    """Inputs for building a run artifact hash bundle."""

    config_payload: Mapping[str, JsonValue]
    schedule_payload: Mapping[str, JsonValue]
    provenance_payload: Mapping[str, JsonValue] | None = None
    sprt_payload: Mapping[str, JsonValue] | None = None
    resume_contract_version: int = RESUME_CONTRACT_VERSION


def build_run_artifact_hash_bundle(request: RunArtifactHashRequest) -> RunArtifactHashBundle:
    """Build all currently-known run artifact hashes from typed inputs."""

    config_digest = config_fingerprint(request.config_payload)
    schedule_digest = schedule_hash(request.schedule_payload)
    provenance_digest = provenance_hash(request.provenance_payload) if request.provenance_payload is not None else None
    sprt_digest = sprt_test_def_hash(request.sprt_payload)
    resume_digest = None
    if provenance_digest is not None:
        resume_digest = resume_hash(
            ResumeHashRequest(
                schedule_hash=schedule_digest,
                provenance_hash=provenance_digest,
                sprt_test_def_hash=sprt_digest,
                resume_contract_version=request.resume_contract_version,
            )
        )
    return RunArtifactHashBundle(
        config_fingerprint=config_digest,
        schedule_hash=schedule_digest,
        provenance_hash=provenance_digest,
        sprt_test_def_hash=sprt_digest,
        resume_hash=resume_digest,
    )


def _typed_digest(kind: str, payload: Mapping[str, JsonValue]) -> str:
    envelope: JsonObject = {
        "schema_version": RUN_ARTIFACT_HASH_SCHEMA_VERSION,
        "kind": kind,
        "payload": _mapping_to_json_object(payload),
    }
    return canonical_sha256(envelope)


def _mapping_to_json_object(payload: Mapping[str, JsonValue]) -> JsonObject:
    return {str(key): json_serialize(value) for key, value in payload.items()}


__all__ = [
    "RESUME_CONTRACT_VERSION",
    "RUN_ARTIFACT_HASH_SCHEMA_VERSION",
    "ResumeHashRequest",
    "RunArtifactHashBundle",
    "RunArtifactHashRequest",
    "build_run_artifact_hash_bundle",
    "build_sprt_test_definition",
    "canonical_json_bytes",
    "canonical_sha256",
    "config_fingerprint",
    "provenance_hash",
    "resume_hash",
    "schedule_hash",
    "sprt_test_def_hash",
]
