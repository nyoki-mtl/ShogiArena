from __future__ import annotations

from .reporting import NullProgressReporter, ProgressPayload


class TqdmProgressReporter(NullProgressReporter):
    """ProgressReporter implementation backed by tqdm."""

    def __init__(self, *, total: int | None = None, desc: str = "shogiarena", unit: str = "game") -> None:
        try:
            from tqdm import tqdm
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("tqdm is required for TqdmProgressReporter (uv add tqdm)") from exc
        self._bar = tqdm(total=total, desc=desc, unit=unit)
        self._completed = 0

    def on_game_complete(self, payload: ProgressPayload) -> None:
        completed = payload.get("completed_games")
        total = payload.get("total_games")
        if isinstance(total, int) and total > 0:
            self._bar.total = total
        if isinstance(completed, int):
            delta = completed - self._completed
            if delta > 0:
                self._bar.update(delta)
                self._completed = completed
            return
        self._bar.update(1)
        self._completed += 1

    def finalize(self, payload: ProgressPayload) -> None:
        self._bar.close()


__all__ = ["TqdmProgressReporter"]
