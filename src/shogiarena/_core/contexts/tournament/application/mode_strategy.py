"""Mode strategy for tournament runner semantics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str

DashboardProfile = Literal["tournament", "spsa", "match", "sprt", "generate"]
SummarySource = Literal["sprt", "generate", "match", "tournament"]


class TournamentModeStrategy(Protocol):
    """Mode-specific behavior contract for tournament runner."""

    @property
    def dashboard_profiles(self) -> tuple[DashboardProfile, ...]: ...

    @property
    def summary_source(self) -> SummarySource: ...

    def is_generate_run(self) -> bool: ...

    def tournament_type(self) -> str: ...


@runtime_checkable
class _ModeConfigPort(Protocol):
    sprt: object | None
    generate: object | None
    experiment_name: str | None


@dataclass(frozen=True)
class _StaticModeStrategy:
    dashboard_profiles: tuple[DashboardProfile, ...]
    summary_source: SummarySource
    is_generate_run_mode: bool = False
    tournament_type_value: str | None = None

    def is_generate_run(self) -> bool:
        return self.is_generate_run_mode

    def tournament_type(self) -> str:
        if self.tournament_type_value is None:
            raise RuntimeError("tournament_type_value must be resolved at construction time")
        return self.tournament_type_value


@dataclass(frozen=True, slots=True)
class _ModeConfigView:
    sprt: object | None
    generate: object | None
    experiment_name: str


def _read_mode_config(config: _ModeConfigPort) -> _ModeConfigView:
    raw_experiment_name = config.experiment_name
    normalized_experiment_name = (coerce_str(raw_experiment_name) or "").strip().lower()
    return _ModeConfigView(
        sprt=config.sprt,
        generate=config.generate,
        experiment_name=normalized_experiment_name,
    )


def _coerce_mode_config(config: object) -> _ModeConfigPort:
    if not isinstance(config, _ModeConfigPort):
        raise TypeError("config must expose sprt/generate/experiment_name")
    return config


def resolve_tournament_mode_strategy(config: object, *, scheduler_name: str = "") -> TournamentModeStrategy:
    """Resolve runner mode strategy from config values."""

    state = _read_mode_config(_coerce_mode_config(config))
    if state.sprt is not None:
        return _StaticModeStrategy(
            dashboard_profiles=("sprt",),
            summary_source="sprt",
            tournament_type_value="sprt",
        )

    if state.generate is not None:
        return _StaticModeStrategy(
            dashboard_profiles=("generate",),
            summary_source="generate",
            is_generate_run_mode=True,
            tournament_type_value="generate",
        )

    exp_name = state.experiment_name
    if exp_name == "match":
        return _StaticModeStrategy(
            dashboard_profiles=("match",),
            summary_source="match",
            tournament_type_value="match",
        )
    if exp_name == "generate":
        return _StaticModeStrategy(
            dashboard_profiles=("generate",),
            summary_source="generate",
            is_generate_run_mode=True,
            tournament_type_value="generate",
        )
    return _StaticModeStrategy(
        dashboard_profiles=("tournament",),
        summary_source="tournament",
        tournament_type_value=scheduler_name or "tournament",
    )


__all__ = [
    "TournamentModeStrategy",
    "resolve_tournament_mode_strategy",
]
