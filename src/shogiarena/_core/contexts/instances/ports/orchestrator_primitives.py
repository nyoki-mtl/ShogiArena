"""Common orchestration helpers shared across tournament and SPSA flows."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from shogiarena._core.contexts.instances.application.instance_models import Instance
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str


@runtime_checkable
class _AdjudicationSettingsPort(Protocol):
    is_max_plies_enabled: bool
    max_plies: int | None


@runtime_checkable
class _RulesPort(Protocol):
    adjudication: _AdjudicationSettingsPort


def max_plies_from_rules(rules: object) -> int:
    """Extract max plies from adjudication settings when enabled."""
    if not isinstance(rules, _RulesPort):
        return 0
    adj_settings = rules.adjudication
    if not adj_settings.is_max_plies_enabled:
        return 0
    value = adj_settings.max_plies
    return int(value) if value is not None else 0


def remote_project_root(remote_instance: Instance) -> str:
    """Return the remote project root for an SSH instance (defaults to $HOME)."""
    candidate = remote_instance.config.project_root
    if s := coerce_str(candidate):
        return s
    return "$HOME/ShogiArena-remote"


def compute_pool_capacity(num_workers: int, is_same_name: bool) -> int:
    """Compute engine pool capacity based on worker count and engine symmetry."""
    cap = num_workers * (2 if is_same_name else 1)
    return max(2, cap)


def make_role_pool_key(name: str, role: str) -> str:
    """Return the pool key used to differentiate role-assigned engines."""
    return f"{name}#{role}"


__all__ = [
    "compute_pool_capacity",
    "make_role_pool_key",
    "max_plies_from_rules",
    "remote_project_root",
]
