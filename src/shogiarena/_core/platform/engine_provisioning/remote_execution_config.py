"""Remote execution runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class RemoteSyncMode(Enum):
    GIT = "git"
    OVERLAY = "overlay"

    @classmethod
    def from_env(cls, raw: str | None) -> RemoteSyncMode:
        if raw is None:
            return cls.GIT
        normalized = raw.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"Unsupported ARENA_REMOTE_SYNC_MODE: {raw}")


@dataclass(frozen=True)
class RemoteExecutionConfig:
    github_token: str | None
    should_export_github_token: bool
    override_ref: str | None
    sync_mode: RemoteSyncMode

    @classmethod
    def from_env(cls) -> RemoteExecutionConfig:
        token = os.environ.get("ARENA_REMOTE_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or None
        export_flag = os.environ.get("ARENA_REMOTE_EXPORT_GITHUB_TOKEN", "").strip().lower()
        should_export_github_token = export_flag in {"1", "true", "yes", "on"}
        override_ref = os.environ.get("ARENA_REMOTE_REF") or None
        sync_mode = RemoteSyncMode.from_env(os.environ.get("ARENA_REMOTE_SYNC_MODE"))
        return cls(
            github_token=token,
            should_export_github_token=should_export_github_token,
            override_ref=override_ref,
            sync_mode=sync_mode,
        )


__all__ = ["RemoteExecutionConfig", "RemoteSyncMode"]
