"""Run metadata persistence service shared by session runners."""

from __future__ import annotations

import json
import logging
import os
import platform
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata as importlib_metadata
from pathlib import Path

import yaml

from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.run_artifact_contract import (
    build_engine_manifest_payloads,
    build_provenance_payload,
    build_schedule_payload,
    total_scheduled_games,
)
from shogiarena._core.shared.kernel.run_artifact_hashes import (
    RunArtifactHashBundle,
    RunArtifactHashRequest,
    build_run_artifact_hash_bundle,
    canonical_sha256,
)
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunManifestInputsSnapshot:
    """Phase-1 manifest input snapshot."""

    config_payload: JsonObject
    hashes: RunArtifactHashBundle
    is_written: bool


@dataclass(frozen=True, slots=True)
class RunManifestSealResult:
    """Phase-2 sealed manifest result."""

    hashes: RunArtifactHashBundle
    manifest: JsonObject


class RunManifestSealError(ValueError):
    """Raised when a manifest cannot be sealed against current inputs."""


class RunMetadataPersistenceService:
    """Persist the v2 manifest and human-readable input echoes for a run."""

    def write_inputs_only_manifest(
        self,
        *,
        run_dir: Path,
        config_payload: Mapping[str, object],
        package_name: str = "shogiarena",
    ) -> RunManifestInputsSnapshot:
        """Persist phase-1 manifest data and return the frozen input snapshot."""

        canonical_config = self._canonical_config_payload(config_payload)
        self._write_config_resolved(run_dir=run_dir, config_payload=canonical_config)
        hashes = self._build_input_hashes(canonical_config)
        manifest_path = run_dir / "manifest.json"
        if manifest_path.exists():
            return RunManifestInputsSnapshot(config_payload=canonical_config, hashes=hashes, is_written=False)

        manifest = self._build_run_manifest(
            run_dir=run_dir,
            input_config_payload=canonical_config,
            resolved_config_payload=canonical_config,
            package_name=package_name,
            status="inputs_only",
            created_at=datetime.now(UTC).isoformat(),
            hashes=hashes,
        )
        try:
            write_json_atomic(manifest_path, manifest)
        except (OSError, TypeError, ValueError) as exc:
            logger.exception("Failed to write inputs-only run manifest to %s: %s", manifest_path, exc)
            raise
        return RunManifestInputsSnapshot(config_payload=canonical_config, hashes=hashes, is_written=True)

    def seal_provenance_manifest(
        self,
        *,
        run_dir: Path,
        inputs_config_payload: Mapping[str, object],
        resolved_config_payload: Mapping[str, object],
        package_name: str = "shogiarena",
    ) -> RunManifestSealResult:
        """Seal phase-2 provenance after artifact/path resolution."""

        canonical_inputs = self._canonical_config_payload(inputs_config_payload)
        canonical_resolved = self._canonical_config_payload(resolved_config_payload)
        manifest_path = run_dir / "manifest.json"
        try:
            persisted = self._load_manifest(manifest_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Failed to read run manifest before sealing %s: %s", manifest_path, exc)
            raise RunManifestSealError(f"failed to read manifest.json in run directory: {run_dir}") from exc
        if persisted is None:
            raise RunManifestSealError(f"manifest.json not found in run directory: {run_dir}")

        status = coerce_str(persisted.get("status"))
        if status not in {"inputs_only", "provenance_sealed"}:
            raise RunManifestSealError(f"unsupported manifest status: {status}")

        input_hashes = self._build_input_hashes(canonical_inputs)
        if persisted.get("inputs_hash") != input_hashes.config_fingerprint:
            raise RunManifestSealError("manifest inputs_hash changed between inputs_only and provenance_sealed writes")

        created_at = coerce_str(persisted.get("created_at")) or datetime.now(UTC).isoformat()
        sealed_hashes = self._build_sealed_hashes(canonical_inputs, canonical_resolved)
        manifest = self._build_run_manifest(
            run_dir=run_dir,
            input_config_payload=canonical_inputs,
            resolved_config_payload=canonical_resolved,
            package_name=package_name,
            status="provenance_sealed",
            created_at=created_at,
            hashes=sealed_hashes,
        )
        hashes = self._object_or_empty(manifest.get("hashes"))
        resume_hash = coerce_str(hashes.get("resume_hash"))
        if resume_hash is None:
            raise RunManifestSealError("sealed manifest requires resume_hash")

        if status == "provenance_sealed":
            existing_resume_hash = coerce_str(self._object_or_empty(persisted.get("hashes")).get("resume_hash"))
            if existing_resume_hash != resume_hash:
                raise RunManifestSealError("sealed manifest resume_hash does not match current provenance")
            persisted_space = self._object_or_empty(persisted.get("inputs")).get("spsa_normalized_space")
            expected_space = self._object_or_empty(manifest.get("inputs")).get("spsa_normalized_space")
            if persisted_space != expected_space:
                raise RunManifestSealError("sealed manifest normalized space digest does not match current provenance")
            self._validate_spsa_normalized_space(run_dir=run_dir, manifest=manifest)
            return RunManifestSealResult(hashes=sealed_hashes, manifest=persisted)

        self._validate_spsa_normalized_space(run_dir=run_dir, manifest=manifest)
        try:
            write_json_atomic(manifest_path, manifest)
        except (OSError, TypeError, ValueError) as exc:
            logger.exception("Failed to seal run manifest to %s: %s", manifest_path, exc)
            raise
        return RunManifestSealResult(hashes=sealed_hashes, manifest=manifest)

    def build_sealed_hashes(
        self,
        *,
        inputs_config_payload: Mapping[str, object],
        resolved_config_payload: Mapping[str, object],
    ) -> RunArtifactHashBundle:
        """Build current sealed hashes without writing a manifest."""

        canonical_inputs = self._canonical_config_payload(inputs_config_payload)
        canonical_resolved = self._canonical_config_payload(resolved_config_payload)
        return self._build_sealed_hashes(canonical_inputs, canonical_resolved)

    def _write_config_resolved(
        self,
        *,
        run_dir: Path,
        config_payload: Mapping[str, object],
    ) -> None:
        resolved_path = run_dir / "inputs" / "config_resolved.yaml"
        if resolved_path.exists():
            return
        try:
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            resolved_path.write_text(yaml.safe_dump(dict(config_payload), sort_keys=False), encoding="utf-8")
        except (OSError, TypeError, ValueError) as exc:
            logger.exception("Failed to write resolved config to %s: %s", resolved_path, exc)

    def _build_run_manifest(
        self,
        *,
        run_dir: Path,
        input_config_payload: JsonObject,
        resolved_config_payload: JsonObject,
        package_name: str,
        status: str,
        created_at: str,
        hashes: RunArtifactHashBundle | None = None,
    ) -> JsonObject:
        tournament = self._object_or_empty(input_config_payload.get("tournament"))
        generate = self._object_or_empty(input_config_payload.get("generate"))
        rules = self._object_or_empty(input_config_payload.get("rules"))
        records_output = self._object_or_empty(input_config_payload.get("records_output"))
        schedule_payload = build_schedule_payload(input_config_payload)
        if hashes is None:
            hashes = (
                self._build_sealed_hashes(input_config_payload, resolved_config_payload)
                if status == "provenance_sealed"
                else self._build_input_hashes(input_config_payload)
            )
        inputs_hash = hashes.config_fingerprint
        sprt_payload = self._object_or_empty(input_config_payload.get("sprt"))
        spsa_payload = self._object_or_empty(schedule_payload.get("spsa"))
        engine_manifest_payloads: list[JsonObject] = []
        hash_source = None
        tunable_handshake: JsonObject | None = None
        normalized_space: JsonObject | None = None
        fixed_option_preflight: JsonObject | None = None
        remote_worker_bundle: JsonObject | None = None
        if status == "provenance_sealed":
            engine_manifest_payloads = build_engine_manifest_payloads(resolved_config_payload)
            provenance_payload = build_provenance_payload(resolved_config_payload)
            hash_source = provenance_payload.get("hash_source")
            raw_tunable_handshake = provenance_payload.get("spsa_tunable_handshake")
            if isinstance(raw_tunable_handshake, dict):
                tunable_handshake = raw_tunable_handshake
                raw_normalized_space = tunable_handshake.get("normalized_space")
                if not isinstance(raw_normalized_space, dict):
                    raise RunManifestSealError("SPSA tunable handshake requires normalized_space")
                normalized_space = self._object_or_empty(raw_normalized_space)
            raw_fixed_option_preflight = provenance_payload.get("spsa_fixed_option_preflight")
            if isinstance(raw_fixed_option_preflight, dict):
                fixed_option_preflight = raw_fixed_option_preflight
            raw_remote_worker_bundle = provenance_payload.get("remote_worker_bundle")
            if isinstance(raw_remote_worker_bundle, dict):
                remote_worker_bundle = raw_remote_worker_bundle
        return {
            "schema_version": 2,
            "status": status,
            "experiment_name": coerce_str(input_config_payload.get("experiment_name")),
            "shogiarena_version": self._detect_package_version(package_name),
            "created_at": created_at,
            "run_dir": str(run_dir),
            "environment": {"orchestrator": self._platform_payload()},
            "inputs_hash": inputs_hash,
            "hashes": hashes.to_payload(),
            "hash_source": hash_source,
            "schedule": {
                "path": "schedule.json",
                "hash": hashes.schedule_hash,
                "kind": coerce_str(schedule_payload.get("kind")),
            },
            "state": {"path": "state.json"},
            "database": {"path": "game.db"},
            "inputs": {
                "config_resolved": "inputs/config_resolved.yaml",
                **(
                    {
                        "spsa_tunable_handshake": {
                            "path": "spsa/tunable_handshake.json",
                            "sha256": canonical_sha256(tunable_handshake),
                        },
                        "spsa_normalized_space": {
                            "path": "spsa/space.normalized.json",
                            "sha256": canonical_sha256(normalized_space),
                        },
                        **(
                            {
                                "spsa_fixed_option_preflight": {
                                    "path": "spsa/fixed_option_preflight.json",
                                    "sha256": canonical_sha256(fixed_option_preflight),
                                }
                            }
                            if fixed_option_preflight is not None
                            else {}
                        ),
                    }
                    if tunable_handshake is not None
                    else {}
                ),
                **(
                    {
                        "remote_worker_bundle": {
                            "path": "remote-worker-bundle.zip",
                            **remote_worker_bundle,
                        }
                    }
                    if remote_worker_bundle is not None
                    else {}
                ),
            },
            "tournament": self._tournament_payload(
                tournament=tournament,
                generate=generate,
                total_scheduled_games=total_scheduled_games(input_config_payload),
            ),
            "sprt": sprt_payload or None,
            "spsa": spsa_payload or None,
            "rules": rules,
            "records_output": records_output or None,
            "engines": engine_manifest_payloads,
        }

    def _build_input_hashes(self, config_payload: JsonObject) -> RunArtifactHashBundle:
        return build_run_artifact_hash_bundle(
            RunArtifactHashRequest(
                config_payload=config_payload,
                schedule_payload=build_schedule_payload(config_payload),
                provenance_payload=None,
                sprt_payload=self._object_or_empty(config_payload.get("sprt")) or None,
            )
        )

    @classmethod
    def _validate_spsa_normalized_space(cls, *, run_dir: Path, manifest: Mapping[str, object]) -> None:
        normalized_entry = cls._object_or_empty(
            cls._object_or_empty(manifest.get("inputs")).get("spsa_normalized_space")
        )
        if not normalized_entry:
            return
        if normalized_entry.get("path") != "spsa/space.normalized.json":
            raise RunManifestSealError("sealed manifest normalized space path is invalid")
        expected_sha256 = coerce_str(normalized_entry.get("sha256"))
        if expected_sha256 is None:
            raise RunManifestSealError("sealed manifest normalized space digest is missing")
        normalized_path = run_dir / "spsa" / "space.normalized.json"
        try:
            raw = json.loads(normalized_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RunManifestSealError("sealed normalized space artifact is missing or invalid") from exc
        if not isinstance(raw, Mapping):
            raise RunManifestSealError("sealed normalized space artifact must contain an object")
        if canonical_sha256(cls._object_or_empty(raw)) != expected_sha256:
            raise RunManifestSealError("sealed normalized space artifact digest mismatch")

    def _build_sealed_hashes(
        self,
        input_config_payload: JsonObject,
        resolved_config_payload: JsonObject,
    ) -> RunArtifactHashBundle:
        return build_run_artifact_hash_bundle(
            RunArtifactHashRequest(
                config_payload=input_config_payload,
                schedule_payload=build_schedule_payload(input_config_payload),
                provenance_payload=build_provenance_payload(resolved_config_payload),
                sprt_payload=self._object_or_empty(input_config_payload.get("sprt")) or None,
            )
        )

    def _platform_payload(self) -> JsonObject:
        return {
            "os": platform.system(),
            "os_release": platform.release(),
            "python": platform.python_version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cpu_model": platform.processor() or None,
            "logical_cpus": coerce_int(os.cpu_count()),
        }

    def _tournament_payload(
        self,
        *,
        tournament: JsonObject,
        generate: JsonObject,
        total_scheduled_games: int | None,
    ) -> JsonObject:
        return {
            "scheduler": coerce_str(tournament.get("scheduler")),
            "games_per_pair": coerce_int(tournament.get("games_per_pair")),
            "num_parallel": coerce_int(tournament.get("num_parallel")) or coerce_int(generate.get("num_parallel")),
            "seed": coerce_int(tournament.get("seed")) or coerce_int(generate.get("seed")),
            "game_order": coerce_str(tournament.get("game_order")),
            "total_scheduled_games": total_scheduled_games,
        }

    @staticmethod
    def _object_or_empty(value: JsonValue | object) -> JsonObject:
        if not isinstance(value, Mapping):
            return {}
        return {str(key): json_serialize(item) for key, item in value.items()}

    @staticmethod
    def _canonical_config_payload(config_payload: Mapping[str, object]) -> JsonObject:
        return {str(key): json_serialize(value) for key, value in config_payload.items()}

    @staticmethod
    def _load_manifest(path: Path) -> JsonObject | None:
        if not path.exists():
            return None
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, Mapping):
            raise ValueError(f"manifest.json must contain an object: {path}")
        return {str(key): json_serialize(value) for key, value in loaded.items()}

    @staticmethod
    def _list_of_objects(value: JsonValue | object) -> list[JsonObject]:
        if not isinstance(value, list):
            return []
        return [RunMetadataPersistenceService._object_or_empty(item) for item in value]

    @staticmethod
    def _detect_package_version(package_name: str) -> str:
        try:
            return importlib_metadata.version(package_name)
        except importlib_metadata.PackageNotFoundError:  # pragma: no cover - environment without package
            return "unknown"


__all__ = [
    "RunManifestInputsSnapshot",
    "RunManifestSealError",
    "RunManifestSealResult",
    "RunMetadataPersistenceService",
]
