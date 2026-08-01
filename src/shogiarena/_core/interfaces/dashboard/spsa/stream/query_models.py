"""Query parsing model for the durable SPSA revision feed."""

from __future__ import annotations

from shogiarena._core.interfaces.dashboard.api_query_models import SummaryStreamQuery


class RevisionStreamQuery(SummaryStreamQuery):
    poll_interval: float = 1.0


__all__ = ["RevisionStreamQuery"]
