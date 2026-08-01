"""Request-scoped remote resource placement policy."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

_PREPLACED_RESOURCES_ENV = "SHOGIARENA_REMOTE_PREPLACED_RESOURCES"


@dataclass(frozen=True, slots=True)
class PreplacedResource:
    """Operator-declared remote path and expected digest."""

    remote_path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class RemoteProvisioningPolicy:
    """Effective placement mode for one CLI execution context."""

    mode: str
    preplaced_resources: Mapping[str, PreplacedResource]


_POLICY: ContextVar[RemoteProvisioningPolicy | None] = ContextVar(
    "shogiarena_remote_provisioning_policy",
    default=None,
)


def current_remote_provisioning_policy() -> RemoteProvisioningPolicy:
    """Current async contextのremote placement policyを返す。"""

    policy = _POLICY.get()
    if policy is None:
        return RemoteProvisioningPolicy(mode="cas", preplaced_resources={})
    return policy


def resolve_remote_provisioning_policy(mode: str) -> RemoteProvisioningPolicy:
    """CLI入力を副作用なしで検証し、effective policyへ解決する。"""
    if mode == "cas":
        return RemoteProvisioningPolicy(mode="cas", preplaced_resources={})
    if mode == "preplaced":
        return RemoteProvisioningPolicy(
            mode="preplaced",
            preplaced_resources=_parse_preplaced_resources(os.environ.get(_PREPLACED_RESOURCES_ENV)),
        )
    raise ValueError(f"unsupported remote provisioning mode: {mode}")


@contextmanager
def remote_provisioning_scope(mode: str | RemoteProvisioningPolicy) -> Iterator[None]:
    """解決済みremote placement policyをasync contextへ封入する。"""

    policy = resolve_remote_provisioning_policy(mode) if isinstance(mode, str) else mode
    token = _POLICY.set(policy)
    try:
        yield
    finally:
        _POLICY.reset(token)


def _parse_preplaced_resources(raw: str | None) -> dict[str, PreplacedResource]:
    if not raw:
        raise ValueError(
            f"preplaced mode requires {_PREPLACED_RESOURCES_ENV} JSON, for example "
            '\'{"engine-linux":{"path":"/opt/engines/engine","sha256":"<64 lowercase hex>"}}\''
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{_PREPLACED_RESOURCES_ENV} must contain valid JSON") from exc
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"{_PREPLACED_RESOURCES_ENV} must contain a non-empty JSON object")
    resources: dict[str, PreplacedResource] = {}
    for logical_id, value in payload.items():
        if not isinstance(logical_id, str) or not logical_id or not isinstance(value, dict):
            raise ValueError(f"{_PREPLACED_RESOURCES_ENV} entries must map logical IDs to objects")
        if set(value) != {"path", "sha256"}:
            raise ValueError(f"{_PREPLACED_RESOURCES_ENV}.{logical_id} requires exactly path and sha256")
        path = value.get("path")
        digest = value.get("sha256")
        if not isinstance(path, str) or not path.startswith("/") or "\\" in path:
            raise ValueError(f"{_PREPLACED_RESOURCES_ENV}.{logical_id}.path must be an absolute POSIX path")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError(f"{_PREPLACED_RESOURCES_ENV}.{logical_id}.sha256 must be lowercase SHA-256")
        resources[logical_id] = PreplacedResource(remote_path=path, sha256=digest)
    return resources


__all__ = [
    "PreplacedResource",
    "RemoteProvisioningPolicy",
    "current_remote_provisioning_policy",
    "remote_provisioning_scope",
    "resolve_remote_provisioning_policy",
]
