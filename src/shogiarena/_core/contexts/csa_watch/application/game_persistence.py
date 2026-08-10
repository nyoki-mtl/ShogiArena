"""Persist finished CSA games without letting a derived database stop watching."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from shogiarena._core.contexts.csa_watch.application.game_export import (
    ExportFailure,
    attach_csa_attributes,
    export_game,
)
from shogiarena._core.contexts.csa_watch.application.run_watcher import RunView
from shogiarena._core.contexts.csa_watch.domain.game_identity import csa_game_key
from shogiarena._core.contexts.csa_watch.ports.record_sink_ports import (
    CsaRecordStorePort,
    CsaRecordStoreRetryableError,
)
from shogiarena._core.contexts.csa_watch.ports.replay_ports import BoardReplayPort
from shogiarena._core.shared.kernel.json_types import JsonObject

logger = logging.getLogger(__name__)

MAX_RETRY_SECONDS = 60.0


@dataclass(frozen=True)
class PersistenceStatus:
    state: str
    attempts: int = 0
    detail: str | None = None
    next_retry_at: float | None = None

    def as_json(self) -> JsonObject:
        return {"state": self.state, "attempts": self.attempts, "detail": self.detail}


class CsaGamePersister:
    """Writes each finished game once and exposes its operational health."""

    def __init__(
        self,
        store: CsaRecordStorePort,
        replayer: BoardReplayPort,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._replayer = replayer
        self._monotonic = monotonic
        self._completed: set[str] = set()
        self._games: dict[str, PersistenceStatus] = {}
        self._runs: dict[str, PersistenceStatus] = {}
        self._suspended_runs: set[str] = set()
        self._revision = 0

    @property
    def persisted_count(self) -> int:
        return len(self._completed)

    @property
    def has_deferred(self) -> bool:
        return any(status.state == "retrying" for status in self._games.values())

    @property
    def revision(self) -> int:
        return self._revision

    def snapshot(self) -> JsonObject:
        return {
            "games": {game_id: status.as_json() for game_id, status in self._games.items()},
            "runs": {run_id: status.as_json() for run_id, status in self._runs.items()},
            "revision": self._revision,
        }

    def status_for_game(self, game_id: str) -> PersistenceStatus | None:
        return self._games.get(game_id)

    def _set_game(self, game_id: str, status: PersistenceStatus) -> None:
        if self._games.get(game_id) == status:
            return
        self._games[game_id] = status
        self._revision += 1

    def _suspend_restarted_run(self, view: RunView) -> None:
        if not view.restarted or view.state.run_id in self._suspended_runs:
            return
        self._suspended_runs.add(view.state.run_id)
        self._runs[view.state.run_id] = PersistenceStatus(
            state="failed",
            detail="イベントログが差し替えられました。game.db の再構築が必要です。",
        )
        self._revision += 1

    def persist_finished(self, views: Sequence[RunView]) -> list[ExportFailure]:
        failures: list[ExportFailure] = []
        now = self._monotonic()
        for view in views:
            self._suspend_restarted_run(view)
            if view.state.run_id in self._suspended_runs:
                continue
            for game in view.state.games:
                game_key = csa_game_key(view.state.run_id, game.game_id)
                if not game.is_finished or game_key in self._completed:
                    continue
                previous = self._games.get(game_key)
                if previous is not None:
                    if previous.state == "failed":
                        continue
                    if previous.next_retry_at is not None and now < previous.next_retry_at:
                        continue

                outcome = export_game(
                    game,
                    self._replayer,
                    should_include_comments=False,
                    should_attach_engine_info=True,
                    storage_game_id=game_key,
                )
                if isinstance(outcome, ExportFailure):
                    self._completed.add(game_key)
                    self._set_game(
                        game_key,
                        PersistenceStatus(state="failed", detail=f"棋譜再生失敗: {outcome.reason}"),
                    )
                    failures.append(outcome)
                    logger.error(
                        "csa game %s not persisted: replay stopped at ply %s (%s): %s",
                        outcome.game_id,
                        outcome.ply,
                        outcome.usi,
                        outcome.reason,
                    )
                    continue

                record = attach_csa_attributes(outcome.record, view.state, game)
                try:
                    self._store.persist(record)
                except CsaRecordStoreRetryableError as exc:
                    attempts = 1 if previous is None else previous.attempts + 1
                    delay = min(MAX_RETRY_SECONDS, float(2 ** (attempts - 1)))
                    self._set_game(
                        game_key,
                        PersistenceStatus(
                            state="retrying",
                            attempts=attempts,
                            detail=str(exc),
                            next_retry_at=now + delay,
                        ),
                    )
                    logger.warning("retrying csa game %s persistence in %.1fs: %s", game.game_id, delay, exc)
                    continue
                except Exception as exc:  # noqa: BLE001 - adapter classified all retryable failures
                    self._set_game(
                        game_key,
                        PersistenceStatus(
                            state="failed",
                            attempts=1,
                            detail=f"{type(exc).__name__}: 永続化できませんでした",
                        ),
                    )
                    logger.error("failed to persist csa game %s permanently: %s", game.game_id, exc, exc_info=exc)
                    continue

                self._completed.add(game_key)
                self._set_game(
                    game_key,
                    PersistenceStatus(state="persisted", attempts=(previous.attempts + 1) if previous else 1),
                )
                logger.info("persisted csa game %s (%s plies)", game.game_id, game.current_ply)
        return failures

    def close(self) -> None:
        self._store.close()


__all__ = ["CsaGamePersister", "MAX_RETRY_SECONDS", "PersistenceStatus"]
