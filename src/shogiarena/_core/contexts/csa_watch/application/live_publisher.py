"""Push folded CSA runs onto the dashboard live stream.

One bridge run is one worker: a run plays one game at a time and many games in
sequence, which is exactly what a worker slot is. Nothing about the live card
cares who is conducting the game, so this is a rename, not a disguise.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from shogiarena._core.contexts.csa_watch.application.live_snapshots import (
    build_games_snapshot,
    build_move_progress,
    build_summary,
    build_worker_snapshot,
)
from shogiarena._core.contexts.csa_watch.application.run_watcher import RunView
from shogiarena._core.contexts.csa_watch.domain.game_identity import csa_game_key
from shogiarena._core.contexts.csa_watch.domain.run_state import HIRATE_SFEN, GameState
from shogiarena._core.contexts.csa_watch.ports.live_stream_ports import CSA_SUMMARY_SOURCE, CsaLiveStreamPort
from shogiarena._core.contexts.csa_watch.ports.replay_ports import BoardReplayPort, ReplayResult
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue

logger = logging.getLogger(__name__)


@dataclass
class _WorkerProgress:
    """What has already been published for one worker slot."""

    game_id: str | None = None
    published_plies: int = 0
    stream_generation: int = 0
    finished_game_id: str | None = None


@dataclass
class _ReplayCache:
    """The last replay of one game, reused while its move list is unchanged.

    Replay always starts from the initial position, never from an intermediate
    SFEN. An SFEN describes a position, not how it was reached, so a board rebuilt
    from one does not know the previous move and renders ``2d`` where the game
    actually reads ``同歩``. Resuming would put a different kifu on the live card
    from the one ``csa export`` writes for the same game.

    A full 512-ply replay costs about 2 ms and only runs when a move arrives, so
    there is nothing to buy back here (``agent-docs/rules/event-loop-cost-budget.md``).
    """

    usi_moves: list[str] = field(default_factory=list)
    result: ReplayResult = ReplayResult(plies=())

    def refresh(self, replayer: BoardReplayPort, initial_sfen: str, moves: list[str]) -> ReplayResult:
        if moves == self.usi_moves and (self.result.plies or not moves):
            return self.result
        self.usi_moves = list(moves)
        self.result = replayer.replay(initial_sfen, moves)
        return self.result


class CsaLivePublisher:
    """Turns watcher output into live-stream messages."""

    def __init__(
        self,
        stream: CsaLiveStreamPort,
        replayer: BoardReplayPort,
        *,
        run_dir: str | None = None,
        persistence_supplier: Callable[[], Mapping[str, JsonValue]] | None = None,
    ) -> None:
        self._stream = stream
        self._replayer = replayer
        self._run_dir = run_dir
        self._persistence_supplier = persistence_supplier
        self._workers: dict[int, _WorkerProgress] = {}
        self._replays: dict[tuple[int, int, str], _ReplayCache] = {}

    def publish(self, changed: tuple[RunView, ...], all_views: tuple[RunView, ...]) -> None:
        """Publish the runs that moved, then the run-wide views."""
        for view in changed:
            try:
                self._publish_run(view)
            except (ValueError, TypeError, KeyError) as exc:
                # A single unpublishable run must not take the watcher down; the
                # log is the source of truth and the next poll will retry.
                logger.warning("failed to publish csa run %s: %s", view.state.run_id, exc, exc_info=exc)
        if changed:
            self.publish_run_views(all_views)

    def publish_run_views(self, all_views: tuple[RunView, ...]) -> None:
        persistence = self._persistence_supplier() if self._persistence_supplier is not None else None
        self._stream.broadcast_games_snapshot(build_games_snapshot(all_views))
        self._stream.broadcast_summary_update(
            build_summary(
                all_views,
                timestamp=datetime.now(tz=UTC).isoformat(),
                run_dir=self._run_dir,
                persistence=persistence,
            ),
            source=CSA_SUMMARY_SOURCE,
        )

    def _current_game(self, view: RunView) -> GameState | None:
        current = view.state.current_game
        if current is not None:
            return current
        if not view.state.games:
            return None
        latest = view.state.games[-1]
        progress = self._workers.get(view.worker_idx)
        if (
            latest.is_finished
            and progress is not None
            and progress.game_id == latest.game_id
            and progress.finished_game_id != latest.game_id
        ):
            return latest
        return None

    def _publish_run(self, view: RunView) -> None:
        game = self._current_game(view)
        if game is None:
            return
        progress = self._workers.setdefault(view.worker_idx, _WorkerProgress())
        if progress.stream_generation != view.stream_generation:
            self._replays = {key: value for key, value in self._replays.items() if key[0] != view.worker_idx}
            progress.game_id = None
            progress.published_plies = 0
            progress.stream_generation = view.stream_generation
            progress.finished_game_id = None
        cache = self._replays.setdefault((view.worker_idx, view.stream_generation, game.game_id), _ReplayCache())
        replay = cache.refresh(self._replayer, game.initial_sfen or HIRATE_SFEN, [move.usi for move in game.moves])
        persistence = self._persistence_supplier() if self._persistence_supplier is not None else None
        snapshot: JsonObject = build_worker_snapshot(view, game, replay, persistence)
        game_key = csa_game_key(view.state.run_id, game.game_id)

        is_new_game = progress.game_id != game.game_id
        if is_new_game:
            progress.game_id = game.game_id
            progress.published_plies = 0
            progress.finished_game_id = None
            # Assignment is what tells the page which game a worker holds; without
            # it the client never subscribes to this game's move stream.
            self._stream.assign_worker_snapshot(view.worker_idx, snapshot)
        else:
            self._stream.set_worker_snapshot(view.worker_idx, snapshot)

        for ply in range(progress.published_plies + 1, game.current_ply + 1):
            self._stream.broadcast_worker_update(
                view.worker_idx,
                build_move_progress(game, ply, replay, game_key=game_key),
            )
        progress.published_plies = game.current_ply
        if game.is_finished:
            progress.finished_game_id = game.game_id

        self._stream.broadcast_worker_update(
            view.worker_idx,
            {
                "type": "csa_state",
                "game_id": game_key,
                "meta": snapshot.get("meta"),
            },
        )


__all__ = ["CsaLivePublisher"]
