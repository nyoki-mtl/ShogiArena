"""Per-game execution contract evidence persistence。"""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, field_validator

from shogiarena._core.contexts.game_session.ports.game_execution_spec import Sha256Digest
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.json_types import JsonObject

_NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_RemoteJobToken = Annotated[str, StringConstraints(pattern=r"^[a-z]+-[0-9a-f]{32}$")]


class _RemoteAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["shogiarena.remote-assignment.v1"]
    instance_id: _NonEmptyText
    endpoint_identity: _NonEmptyText
    deployment_id: Sha256Digest
    job_id: _RemoteJobToken
    attempt_id: _RemoteJobToken
    execution_digest: Sha256Digest
    artifact_digests: list[Sha256Digest]

    @field_validator("artifact_digests")
    @classmethod
    def _require_unique_artifact_digests(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("artifact_digests must be unique")
        return value


def _manifest_path(run_dir: Path, game_id: str) -> Path:
    key = hashlib.sha256(game_id.encode("utf-8")).hexdigest()[:16]
    return run_dir / "game_execution_specs" / f"{key}.json"


def persist_game_execution_manifest(
    *,
    run_dir: Path,
    game_id: str,
    payload: JsonObject,
) -> Path:
    """一局のsealed specとresolver provenanceをatomicに保存する。"""

    path = _manifest_path(run_dir, game_id)
    remote_assignment: JsonObject | None = None
    remote_logical_job_key: str | None = None
    if path.is_file():
        try:
            existing_payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid existing game execution manifest: {path}: {exc}") from exc
        if not isinstance(existing_payload, dict) or existing_payload.get("game_id") != game_id:
            raise ValueError(f"existing game execution manifest identity mismatch: {path}")
        existing_assignment = existing_payload.get("remote_assignment")
        if existing_assignment is not None:
            try:
                validated_assignment = _RemoteAssignment.model_validate(existing_assignment)
            except ValidationError as exc:
                raise ValueError(f"invalid persisted remote assignment: {path}: {exc}") from exc
            spec_payload = payload.get("game_execution_spec")
            execution_digest = spec_payload.get("execution_digest") if isinstance(spec_payload, dict) else None
            if execution_digest != validated_assignment.execution_digest:
                raise ValueError(
                    f"persisted remote assignment conflicts with resumed execution spec for game {game_id}"
                )
            remote_assignment = validated_assignment.model_dump(mode="json")
        existing_job_key = existing_payload.get("remote_logical_job_key")
        if existing_job_key is not None:
            if not isinstance(existing_job_key, str) or not existing_job_key.strip():
                raise ValueError(f"invalid persisted remote logical job key: {path}")
            remote_logical_job_key = existing_job_key
    next_payload: JsonObject = {
        "game_id": game_id,
        **payload,
    }
    if remote_assignment is not None:
        next_payload["remote_assignment"] = remote_assignment
    if remote_logical_job_key is not None:
        next_payload["remote_logical_job_key"] = remote_logical_job_key
    write_json_atomic(
        path,
        next_payload,
    )
    return path


def ensure_remote_logical_job_key(*, run_dir: Path, game_id: str) -> str:
    """Return a run-local durable key for Remote attempt recovery."""

    path = _manifest_path(run_dir, game_id)
    if not path.is_file():
        raise FileNotFoundError(f"game execution manifest is missing before assignment: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid game execution manifest: {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("game_id") != game_id:
        raise ValueError(f"game execution manifest identity mismatch: {path}")
    existing = payload.get("remote_logical_job_key")
    if existing is not None:
        if not isinstance(existing, str) or not existing.strip():
            raise ValueError(f"invalid persisted remote logical job key: {path}")
        return existing

    # Artifacts created before the run-local key existed must recover the old
    # host-side assignment instead of silently dispatching a second attempt.
    if payload.get("remote_assignment") is not None:
        spec = payload.get("game_execution_spec")
        identity = spec.get("identity") if isinstance(spec, dict) else None
        legacy_key = identity.get("job_id") if isinstance(identity, dict) else None
        if not isinstance(legacy_key, str) or not legacy_key.strip():
            raise ValueError(f"legacy remote assignment has no recoverable logical job key: {path}")
        key = legacy_key
    else:
        key = f"run-{secrets.token_hex(16)}:{game_id}"
    payload["remote_logical_job_key"] = key
    write_json_atomic(path, {str(field): value for field, value in payload.items()})
    return key


def persist_remote_game_assignment(
    *,
    run_dir: Path,
    game_id: str,
    assignment: JsonObject,
) -> Path:
    """Persist and resume-validate the selected endpoint/job before dispatch."""

    try:
        validated = _RemoteAssignment.model_validate(assignment)
    except ValidationError as exc:
        raise ValueError(f"invalid remote assignment evidence: {exc}") from exc
    path = _manifest_path(run_dir, game_id)
    if not path.is_file():
        raise FileNotFoundError(f"game execution manifest is missing before assignment: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid game execution manifest: {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("game_id") != game_id:
        raise ValueError(f"game execution manifest identity mismatch: {path}")
    existing = payload.get("remote_assignment")
    normalized = validated.model_dump(mode="json")
    if existing is not None:
        try:
            previous = _RemoteAssignment.model_validate(existing)
        except ValidationError as exc:
            raise ValueError(f"invalid persisted remote assignment: {path}: {exc}") from exc
        if previous != validated:
            raise ValueError(f"persisted remote assignment conflicts with resumed dispatch for game {game_id}")
        return path
    payload["remote_assignment"] = normalized
    write_json_atomic(path, {str(key): value for key, value in payload.items()})
    return path


__all__ = [
    "ensure_remote_logical_job_key",
    "persist_game_execution_manifest",
    "persist_remote_game_assignment",
]
