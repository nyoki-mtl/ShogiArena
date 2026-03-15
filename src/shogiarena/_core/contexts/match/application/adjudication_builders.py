"""Application-level adjudication helpers for match workflows."""

from __future__ import annotations

from shogiarena._core.contexts.match.domain.adjudication import AdjudicationConfig


def max_plies_only_adjudication(max_plies: int) -> AdjudicationConfig:
    """Build an adjudication config that only enforces max-plies."""

    return AdjudicationConfig(
        is_resign_enabled=False,
        is_max_plies_enabled=True,
        max_plies=max_plies,
    )


__all__ = ["max_plies_only_adjudication"]
