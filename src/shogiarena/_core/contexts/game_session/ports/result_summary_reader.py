"""Ports for reading persisted game results for offline summaries."""

from __future__ import annotations

from typing import Protocol

from shogiarena._core.contexts.game_session.domain.result_summary_models import ResultSummaryGameRow


class ResultSummaryReaderPort(Protocol):
    """保存済み対局結果の読み取り契約。"""

    def read_games(self) -> tuple[ResultSummaryGameRow, ...]:
        """集計対象の対局行を返す。"""


__all__ = ["ResultSummaryReaderPort"]
