"""Artifact writing service for tournament summary outputs."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from shogiarena._core.shared.kernel.json_types import JsonObject

logger = logging.getLogger(__name__)


class TournamentSummaryArtifactService:
    """Write summary-related JSON artifacts to run directories."""

    @staticmethod
    def write_summary_btd(
        run_dir: Path,
        payload: JsonObject,
        *,
        log_context: str,
    ) -> None:
        summary_btd_path = run_dir / "summary_btd.json"
        try:
            with open(summary_btd_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(
                "Failed to write %s BTD summary to %s: %s",
                log_context,
                summary_btd_path,
                exc,
                exc_info=True,
            )

    @staticmethod
    def write_tournament_results(run_dir: Path, payload: JsonObject) -> None:
        results_path = run_dir / "tournament_results.json"
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)


__all__ = ["TournamentSummaryArtifactService"]
