"""Durable Remote job lifecycle contract."""

from __future__ import annotations

import secrets
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    GameExecutionResult,
    GameExecutionSpec,
    Sha256Digest,
)

REMOTE_JOB_SCHEMA_VERSION = "shogiarena.remote-job.v1"
RemoteJobToken = Annotated[str, StringConstraints(pattern=r"^[a-z]+-[0-9a-f]{32}$")]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class RemoteJobState(StrEnum):
    """Durable Remote job state."""

    CREATED = "created"
    PREPARING = "preparing"
    PREPARED = "prepared"
    STARTING = "starting"
    RUNNING = "running"
    CANCELLING = "cancelling"
    TERMINAL = "terminal"
    ACKNOWLEDGED = "acknowledged"
    COLLECTABLE = "collectable"


class _RemoteJobModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RemoteJobIdentity(_RemoteJobModel):
    """Logical gameと一回の実行attemptを分離するidentity."""

    schema_version: Literal["shogiarena.remote-job.v1"] = REMOTE_JOB_SCHEMA_VERSION
    job_id: RemoteJobToken
    logical_game_id: NonEmptyText
    attempt_id: RemoteJobToken
    execution_digest: Sha256Digest


class RemoteJobStatus(_RemoteJobModel):
    """Atomic status.json contract."""

    schema_version: Literal["shogiarena.remote-job.v1"] = REMOTE_JOB_SCHEMA_VERSION
    identity: RemoteJobIdentity
    state: RemoteJobState
    revision: int = Field(ge=0)
    updated_at: NonEmptyText
    process_group_id: int | None = Field(default=None, gt=0)
    terminal_kind: Literal["completed", "failed", "timed_out", "cancelled"] | None = None
    error: NonEmptyText | None = None
    acknowledged_at: NonEmptyText | None = None
    process_alive: bool | None = None
    heartbeat_stale: bool | None = None
    diagnostic: NonEmptyText | None = None

    @model_validator(mode="after")
    def _validate_terminal_fields(self) -> Self:
        terminal_states = {
            RemoteJobState.TERMINAL,
            RemoteJobState.ACKNOWLEDGED,
            RemoteJobState.COLLECTABLE,
        }
        if self.state in terminal_states and self.terminal_kind is None:
            raise ValueError("terminal remote job state requires terminal_kind")
        if self.state not in terminal_states and (
            self.terminal_kind is not None or self.error is not None or self.acknowledged_at is not None
        ):
            raise ValueError("non-terminal remote job state must not contain terminal fields")
        if self.state in {RemoteJobState.ACKNOWLEDGED, RemoteJobState.COLLECTABLE}:
            if self.acknowledged_at is None:
                raise ValueError("acknowledged remote job state requires acknowledged_at")
        elif self.acknowledged_at is not None:
            raise ValueError("acknowledged_at is only valid after acknowledgement")
        return self


class RemoteJobHeartbeat(_RemoteJobModel):
    """Atomic heartbeat.json contract."""

    schema_version: Literal["shogiarena.remote-job.v1"] = REMOTE_JOB_SCHEMA_VERSION
    job_id: RemoteJobToken
    execution_digest: Sha256Digest
    sequence: int = Field(ge=0)
    observed_at: NonEmptyText


class RemoteJobResult(_RemoteJobModel):
    """Validated terminal result envelope."""

    schema_version: Literal["shogiarena.remote-job.v1"] = REMOTE_JOB_SCHEMA_VERSION
    job_id: RemoteJobToken
    execution_digest: Sha256Digest
    logical_game_id: NonEmptyText
    payload: GameExecutionResult


def create_remote_job_identity(
    spec: GameExecutionSpec,
    *,
    attempt_id: str | None = None,
) -> RemoteJobIdentity:
    """Cryptographically unique job/attempt identityを生成する。"""

    return RemoteJobIdentity(
        job_id=f"job-{secrets.token_hex(16)}",
        logical_game_id=spec.identity.game_id,
        attempt_id=attempt_id or f"attempt-{secrets.token_hex(16)}",
        execution_digest=spec.execution_digest,
    )


__all__ = [
    "REMOTE_JOB_SCHEMA_VERSION",
    "RemoteJobHeartbeat",
    "RemoteJobIdentity",
    "RemoteJobResult",
    "RemoteJobState",
    "RemoteJobStatus",
    "create_remote_job_identity",
]
