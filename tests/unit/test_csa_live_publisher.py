"""CSA 対局をライブダッシュボードへ流す publisher（0068）。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from shogiarena._core.contexts.csa_watch.adapters.rsshogi_replay import RsshogiBoardReplay
from shogiarena._core.contexts.csa_watch.application.live_publisher import CsaLivePublisher
from shogiarena._core.contexts.csa_watch.application.live_snapshots import (
    build_games_snapshot,
    build_summary,
    build_worker_snapshot,
)
from shogiarena._core.contexts.csa_watch.application.run_watcher import RunView, apply_chunk
from shogiarena._core.contexts.csa_watch.domain.game_identity import csa_game_key
from shogiarena._core.contexts.csa_watch.domain.result_mapping import arena_game_result
from shogiarena._core.contexts.csa_watch.domain.run_state import RunState
from shogiarena._core.contexts.csa_watch.ports.log_source_ports import LogChunk
from shogiarena._core.contexts.dashboard.application.game.state_snapshot_builders import (
    build_ws_snapshot_payload,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_payload_builders import (
    normalize_worker_snapshot_dto,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue

from .test_csa_event_fold import (
    BUOY_HANDICAP,
    ENGINE_DEATH,
    EVEN_COMPLETE,
    PONDER_OUTCOMES,
    fold_fixture,
)

# The frontend consumes this file through `normalizeLiveGameRecord` in a vitest
# test, so the two languages agree on one artifact rather than on a description.
GOLDEN_PATH = Path(
    "src/shogiarena/_core/interfaces/dashboard/frontend/src/modules/csa/__tests__/fixtures/live-game-golden.json"
)

# Same idea for the summary. The page's boot guard reads this shape, so a
# description of it on the Python side is not enough — vitest runs the real
# guard over this artifact.
GOLDEN_SUMMARY_PATH = Path(
    "src/shogiarena/_core/interfaces/dashboard/frontend/src/modules/csa/__tests__/fixtures/summary-golden.json"
)
CONTRACT_FIXTURE_PATH = Path("tests/fixtures/csa_contract/csa-event-log-contract.jsonl")

# Mirrors `GAME_RESULTS` in modules/live/services/updates/normalizers/common.ts.
# A value outside this set makes `normalizeGameResult` throw and takes the page down.
ARENA_GAME_RESULTS = frozenset(
    {
        "BLACK_WIN",
        "WHITE_WIN",
        "DRAW_BY_REPETITION",
        "ERROR",
        "BLACK_WIN_BY_DECLARATION",
        "WHITE_WIN_BY_DECLARATION",
        "DRAW_BY_MAX_PLIES",
        "INVALID",
        "BLACK_WIN_BY_FORFEIT",
        "WHITE_WIN_BY_FORFEIT",
        "DRAW_BY_IMPASSE",
        "PAUSED",
        "BLACK_WIN_BY_ILLEGAL_MOVE",
        "WHITE_WIN_BY_ILLEGAL_MOVE",
        "BLACK_WIN_BY_TIMEOUT",
        "WHITE_WIN_BY_TIMEOUT",
    }
)


@dataclass
class FakeLiveStream:
    """Records what the publisher would have broadcast."""

    assigned: list[tuple[int, JsonObject]] = field(default_factory=list)
    set_snapshots: list[tuple[int, JsonObject]] = field(default_factory=list)
    worker_updates: list[tuple[int, JsonObject]] = field(default_factory=list)
    games_snapshots: list[JsonObject] = field(default_factory=list)
    summaries: list[tuple[str, JsonObject]] = field(default_factory=list)

    def assign_worker_snapshot(self, worker_idx: int, snapshot: Mapping[str, JsonValue]) -> None:
        self.assigned.append((worker_idx, dict(snapshot)))

    def set_worker_snapshot(
        self,
        worker_idx: int,
        snapshot: Mapping[str, JsonValue],
        *,
        should_broadcast: bool = True,
    ) -> None:
        self.set_snapshots.append((worker_idx, dict(snapshot)))

    def broadcast_worker_update(self, worker_idx: int, payload: Mapping[str, JsonValue]) -> None:
        self.worker_updates.append((worker_idx, dict(payload)))

    def broadcast_games_snapshot(self, snapshot: Mapping[str, JsonValue], *, event_type: str = "bulk") -> None:
        self.games_snapshots.append(dict(snapshot))

    def broadcast_summary_update(self, payload: Mapping[str, JsonValue], *, source: str = "tournament") -> None:
        self.summaries.append((source, dict(payload)))


def _view(run_id: str, *, worker_idx: int = 0) -> RunView:
    return RunView(worker_idx=worker_idx, state=fold_fixture(run_id), path=Path(f"{run_id}-events.jsonl"))


def _active_view(run_id: str, *, worker_idx: int = 0) -> RunView:
    view = _view(run_id, worker_idx=worker_idx)
    game = replace(view.state.games[-1], result=None, terminal=(), ended_ts=None)
    state = replace(view.state, games=(game,), current_game_id=game.game_id, phase="playing", stopped=False)
    return replace(view, state=state)


def _contract_view() -> RunView:
    lines = tuple(CONTRACT_FIXTURE_PATH.read_text(encoding="utf-8").splitlines())
    state, _ = apply_chunk(RunState(run_id="1800000000"), LogChunk(lines=lines, has_partial_line=False))
    return RunView(worker_idx=0, state=state, path=CONTRACT_FIXTURE_PATH)


def _snapshot_for(run_id: str) -> JsonObject:
    view = _view(run_id)
    game = view.state.games[-1]
    replay = RsshogiBoardReplay().replay(game.initial_sfen, [move.usi for move in game.moves])
    return build_worker_snapshot(view, game, replay)


class TestWorkerSnapshot:
    def test_moves_and_readings_line_up_with_the_played_game(self) -> None:
        snapshot = _snapshot_for(EVEN_COMPLETE)
        moves = snapshot["moves"]
        ki2 = snapshot["ki2_moves"]
        assert isinstance(moves, list) and isinstance(ki2, list)
        assert len(moves) == snapshot["current_ply"]
        assert len(ki2) == len(moves)

    def test_only_our_own_side_carries_evaluations(self) -> None:
        view = _active_view(EVEN_COMPLETE)
        game = view.state.games[-1]
        snapshot = _snapshot_for(EVEN_COMPLETE)
        ours = snapshot["eval_black"] if game.my_color == "black" else snapshot["eval_white"]
        theirs = snapshot["eval_white"] if game.my_color == "black" else snapshot["eval_black"]
        assert isinstance(ours, list) and isinstance(theirs, list)
        assert any(value is not None for value in ours)
        assert all(value is None for value in theirs)

    def test_per_ply_series_keep_their_holes(self) -> None:
        """A hole must stay a hole: compacting shifts every later value one ply."""
        snapshot = _snapshot_for(EVEN_COMPLETE)
        ply_count = snapshot["current_ply"]
        for key in ("eval_black", "eval_white", "nodes_values", "depth_values", "move_times_ms"):
            series = snapshot[key]
            assert isinstance(series, list)
            assert len(series) == ply_count, key

    def test_the_series_survive_the_arena_snapshot_normalizer(self) -> None:
        snapshot = _snapshot_for(EVEN_COMPLETE)
        normalized = normalize_worker_snapshot_dto(snapshot)
        assert normalized is not None
        assert len(normalized["eval_black"]) == snapshot["current_ply"]
        assert normalized["eval_black"] == snapshot["eval_black"]

    def test_ledger_clocks_are_carried_as_of_their_own_timestamp(self) -> None:
        view = _active_view(EVEN_COMPLETE)
        game = view.state.games[-1]
        replay = RsshogiBoardReplay().replay(game.initial_sfen, [move.usi for move in game.moves])
        snapshot = build_worker_snapshot(view, game, replay)

        assert snapshot["clock_active"] == game.side_to_move
        assert snapshot["black_remain_ms"] == game.black_remaining_ms
        assert snapshot["white_remain_ms"] == game.white_remaining_ms
        assert snapshot["clock_started_at_ms"] == game.ledger_as_of_ts

        normalized = normalize_worker_snapshot_dto(snapshot)
        assert normalized is not None
        ws_snapshot = build_ws_snapshot_payload(game.game_id, normalized)
        assert ws_snapshot is not None
        state = ws_snapshot["state"]
        assert isinstance(state, dict)
        assert state["clock"] == {
            "active": game.side_to_move,
            "black_remain_ms": game.black_remaining_ms,
            "white_remain_ms": game.white_remaining_ms,
            "started_at_ms": game.ledger_as_of_ts,
        }

    def test_csa_state_rides_along_as_metadata(self) -> None:
        snapshot = _snapshot_for(ENGINE_DEATH)
        meta = snapshot["meta"]
        assert isinstance(meta, dict)
        csa = meta["csa"]
        assert isinstance(csa, dict)
        assert csa["fallback_plies"] == [9]
        alerts = csa["alerts"]
        assert isinstance(alerts, list)
        assert alerts[0]["level"] == "error"
        assert alerts[0]["game_id"] == csa["server_game_id"]
        assert csa["games"][-1]["my_color"] in {"black", "white"}
        assert csa["log_health"]["has_partial_line"] is False

    def test_ponder_tally_stays_out_of_the_dashboard_projection(self) -> None:
        snapshot = _snapshot_for(PONDER_OUTCOMES)
        meta = snapshot["meta"]
        assert isinstance(meta, dict)
        assert "ponder" not in meta["csa"]

    def test_a_handicap_start_is_published_as_itself(self) -> None:
        snapshot = _snapshot_for(BUOY_HANDICAP)
        assert isinstance(snapshot["initial_sfen"], str)
        assert snapshot["initial_sfen"].split()[1] == "w"


class TestResultVocabulary:
    @pytest.mark.parametrize(
        ("result", "my_color", "terminal", "expected"),
        [
            ("win", "black", ("%TORYO", "#RESIGN", "#WIN"), "BLACK_WIN"),
            ("win", "white", ("%TORYO", "#RESIGN", "#WIN"), "WHITE_WIN"),
            ("lose", "black", ("%TORYO", "#RESIGN", "#LOSE"), "WHITE_WIN"),
            ("lose", "white", ("%TORYO", "#RESIGN", "#LOSE"), "BLACK_WIN"),
            ("win", "black", ("%KACHI", "#JISHOGI", "#WIN"), "BLACK_WIN_BY_DECLARATION"),
            ("lose", "black", ("%TIME_UP", "#TIME_UP", "#LOSE"), "WHITE_WIN_BY_TIMEOUT"),
            ("lose", "white", ("%ILLEGAL_MOVE", "#ILLEGAL_MOVE", "#LOSE"), "BLACK_WIN_BY_ILLEGAL_MOVE"),
            ("draw", "black", ("%SENNICHITE", "#SENNICHITE", "#DRAW"), "DRAW_BY_REPETITION"),
            ("draw", "black", ("#MAX_MOVES",), "DRAW_BY_MAX_PLIES"),
            ("draw", "black", ("%JISHOGI",), "DRAW_BY_IMPASSE"),
            ("draw", "black", (), "DRAW_BY_REPETITION"),
            ("unknown", "black", ("%CHUDAN",), "PAUSED"),
            (None, "black", (), None),
        ],
    )
    def test_mapping_is_fixed(
        self, result: str | None, my_color: str, terminal: tuple[str, ...], expected: str | None
    ) -> None:
        assert arena_game_result(result, my_color, terminal) == expected  # type: ignore[arg-type]

    def test_every_mapped_value_is_one_the_dashboard_accepts(self) -> None:
        terminals = [
            (),
            ("%TORYO", "#RESIGN", "#WIN"),
            ("%KACHI", "#JISHOGI"),
            ("%TIME_UP",),
            ("%ILLEGAL_MOVE",),
            ("%SENNICHITE",),
            ("#MAX_MOVES",),
            ("%JISHOGI",),
            ("%CHUDAN",),
        ]
        for result in ("win", "lose", "draw", "unknown"):
            for color in ("black", "white"):
                for terminal in terminals:
                    mapped = arena_game_result(result, color, terminal)  # type: ignore[arg-type]
                    assert mapped in ARENA_GAME_RESULTS, (result, color, terminal, mapped)


class TestPublisher:
    def test_a_new_game_is_assigned_before_it_is_updated(self) -> None:
        stream = FakeLiveStream()
        publisher = CsaLivePublisher(stream, RsshogiBoardReplay())
        view = _active_view(EVEN_COMPLETE)
        publisher.publish((view,), (view,))
        assert len(stream.assigned) == 1
        assert stream.set_snapshots == []
        assert stream.assigned[0][0] == 0

    def test_only_unpublished_plies_are_streamed(self) -> None:
        stream = FakeLiveStream()
        publisher = CsaLivePublisher(stream, RsshogiBoardReplay())
        view = _active_view(EVEN_COMPLETE)
        publisher.publish((view,), (view,))
        move_updates = [payload for _, payload in stream.worker_updates if payload.get("type") == "move_progress"]
        assert len(move_updates) == view.state.games[-1].current_ply

        publisher.publish((view,), (view,))
        move_updates_after = [payload for _, payload in stream.worker_updates if payload.get("type") == "move_progress"]
        assert len(move_updates_after) == len(move_updates)

    def test_the_second_publish_updates_rather_than_reassigns(self) -> None:
        stream = FakeLiveStream()
        publisher = CsaLivePublisher(stream, RsshogiBoardReplay())
        view = _active_view(EVEN_COMPLETE)
        publisher.publish((view,), (view,))
        publisher.publish((view,), (view,))
        assert len(stream.assigned) == 1
        assert len(stream.set_snapshots) == 1

    def test_idle_between_games_publishes_terminal_once_without_republishing_stale_board(self) -> None:
        stream = FakeLiveStream()
        publisher = CsaLivePublisher(stream, RsshogiBoardReplay())
        active = _active_view(EVEN_COMPLETE)
        publisher.publish((active,), (active,))

        idle = replace(
            _view(EVEN_COMPLETE),
            state=replace(_view(EVEN_COMPLETE).state, phase="idle", stopped=False, current_game_id=None),
        )
        publisher.publish((idle,), (idle,))
        publisher.publish((idle,), (idle,))

        assert len(stream.assigned) == 1
        assert len(stream.set_snapshots) == 1
        assert stream.set_snapshots[0][1]["game_result"] is not None

    def test_restart_generation_reassigns_even_when_the_game_id_is_reused(self) -> None:
        stream = FakeLiveStream()
        publisher = CsaLivePublisher(stream, RsshogiBoardReplay())
        active = _active_view(EVEN_COMPLETE)
        publisher.publish((active,), (active,))

        restarted = replace(active, stream_generation=1, restarted=True)
        publisher.publish((restarted,), (restarted,))

        assert len(stream.assigned) == 2

    def test_summary_and_games_go_out_under_the_csa_source(self) -> None:
        stream = FakeLiveStream()
        publisher = CsaLivePublisher(stream, RsshogiBoardReplay())
        view = _view(EVEN_COMPLETE)
        publisher.publish((view,), (view,))
        assert stream.summaries
        assert stream.summaries[-1][0] == "csa"
        assert stream.games_snapshots[-1]["total_games"] == 1
        assert len(stream.games_snapshots[-1]["schedule"]) == 1

    def test_kifu_notation_matches_a_full_replay_move_by_move(self) -> None:
        """A resumed replay loses the previous move, and with it every `同` reading.

        The publisher is called once per move, so almost every publish would land
        on a resume boundary; a wrong reading there would also disagree with the
        `.csa` that `csa export` writes for the same game.
        """
        stream = FakeLiveStream()
        publisher = CsaLivePublisher(stream, RsshogiBoardReplay())
        state = fold_fixture(EVEN_COMPLETE)
        game = state.games[-1]

        # Feed the game one move at a time, the way a tailed log arrives.
        for ply in range(1, game.current_ply + 1):
            partial = replace(game, moves=game.moves[:ply], result=None, terminal=())
            partial_state = replace(state, games=(partial,), current_game_id=partial.game_id)
            publisher.publish(
                (RunView(worker_idx=0, state=partial_state, path=Path("x-events.jsonl")),),
                (),
            )

        streamed_ki2 = [
            payload["ki2_move"]
            for _, payload in stream.worker_updates
            if payload.get("type") == "move_progress" and "ki2_move" in payload
        ]
        full = RsshogiBoardReplay().replay(game.initial_sfen, [move.usi for move in game.moves])
        assert streamed_ki2 == list(full.ki2_moves)

    def test_the_final_snapshot_matches_a_full_replay(self) -> None:
        stream = FakeLiveStream()
        publisher = CsaLivePublisher(stream, RsshogiBoardReplay())
        state = fold_fixture(EVEN_COMPLETE)
        game = state.games[-1]
        for ply in range(1, game.current_ply + 1):
            partial = replace(game, moves=game.moves[:ply], result=None, terminal=())
            partial_state = replace(state, games=(partial,), current_game_id=partial.game_id)
            publisher.publish((RunView(worker_idx=0, state=partial_state, path=Path("x")),), ())
        last_snapshot = stream.set_snapshots[-1][1]
        assert last_snapshot["ki2_moves"] == list(_snapshot_for(EVEN_COMPLETE)["ki2_moves"])

    def test_nothing_is_published_for_a_run_with_no_game(self) -> None:
        stream = FakeLiveStream()
        publisher = CsaLivePublisher(stream, RsshogiBoardReplay())
        empty = RunView(worker_idx=0, state=RunState(run_id="r"), path=Path("r-events.jsonl"))
        publisher.publish((empty,), (empty,))
        assert stream.assigned == []
        assert stream.set_snapshots == []


class TestSummaryAndGames:
    def test_an_empty_log_directory_still_produces_a_valid_summary(self) -> None:
        summary = build_summary((), timestamp="2026-08-05T00:00:00+00:00")
        assert summary["is_summary_ready"] is True
        assert summary["tournament_type"] == "csa"
        assert summary["games"] == {"completed": 0, "total": 0, "cancelled": 0}

    def test_the_time_control_is_written_in_the_dialect_the_card_parses(self) -> None:
        """The card reads clocks through `parseTimeControlSpec`.

        That parser needs the leading ``t`` to select time mode at all, and reads
        milliseconds. Seconds without a prefix parse as nothing, and every clock
        on the card silently reads 0:00 — which is what shipped.
        """
        snapshot = _snapshot_for(EVEN_COMPLETE)
        spec = snapshot["time_control_black"]
        assert spec == snapshot["time_control_white"]
        assert isinstance(spec, str)
        assert spec.startswith("t"), spec
        # floodgate-300-10F: 300s total, 10s increment, both in milliseconds.
        assert spec == "t300000+i10000", spec

    def test_the_summary_carries_the_live_view_the_page_guard_demands(self) -> None:
        """``normalizeSummaryEventPayload`` throws without this key, and the page
        turns that throw into a fatal init error, so a summary missing it blanks
        the whole dashboard rather than degrading a single panel.
        """
        summary = build_summary((), timestamp="2026-08-05T00:00:00+00:00")
        live_view = summary["live_view"]
        assert isinstance(live_view, dict)
        progress = live_view["progress"]
        assert isinstance(progress, dict)
        # The guard also rejects a progress block without completed/total.
        assert progress["completed"] == 0
        assert progress["total"] == 0
        assert progress["kind"] == "games"

    def test_the_live_view_progress_tracks_the_games_block(self) -> None:
        view = _view(EVEN_COMPLETE)
        summary = build_summary((view,), timestamp="2026-08-05T00:00:00+00:00")
        live_view = summary["live_view"]
        assert isinstance(live_view, dict)
        games = summary["games"]
        assert isinstance(games, dict)
        assert live_view["progress"] == {
            "kind": "games",
            "unit_label": "games",
            "completed": games["completed"],
            "total": games["total"],
            "cancelled": 0,
            "is_final": False,
            "state": "normal",
            "updated_at": "2026-08-05T00:00:00+00:00",
        }

    def test_games_are_listed_newest_first(self) -> None:
        view = _view(EVEN_COMPLETE)
        snapshot = build_games_snapshot((view,))
        games = snapshot["schedule"]
        assert isinstance(games, list)
        assert games[0]["game_id"] == csa_game_key(view.state.run_id, view.state.games[-1].game_id)
        assert games[0]["server_game_id"] == view.state.games[-1].game_id
        assert games[0]["order"] == 1
        assert games[0]["status"] == "completed"
        assert games[0]["black"] == view.state.games[-1].black_name
        assert games[0]["white"] == view.state.games[-1].white_name
        assert games[0]["total_plies"] == view.state.games[-1].current_ply
        assert snapshot["total_games"] == len(view.state.games)
        assert snapshot["completed_games"] == len(view.state.games)

    def test_same_server_game_id_in_two_runs_has_distinct_internal_keys(self) -> None:
        first = _view(EVEN_COMPLETE, worker_idx=0)
        second_state = replace(first.state, run_id="different-run")
        second = RunView(worker_idx=1, state=second_state, path=Path("different-run-events.jsonl"))

        snapshot = build_games_snapshot((first, second))
        games = snapshot["schedule"]
        assert isinstance(games, list)
        assert len(games) == 2
        assert games[0]["server_game_id"] == games[1]["server_game_id"]
        assert games[0]["game_id"] != games[1]["game_id"]

    def test_games_snapshot_marks_our_side_without_replacing_the_engine_name(self) -> None:
        view = _view(EVEN_COMPLETE)
        snapshot = build_games_snapshot((view,))
        games = snapshot["schedule"]
        assert isinstance(games, list)
        newest = games[0]
        own_side = view.state.games[-1].my_color
        assert newest[own_side] == getattr(view.state.games[-1], f"{own_side}_name")
        assert newest[f"{own_side}_instance"] == "Ours"
        assert newest[f"{own_side}_instance_kind"] == "csa-owned"


def test_golden_snapshot_matches_the_committed_fixture() -> None:
    """The fixture vitest reads must be what the publisher actually emits."""
    produced = _snapshot_for(EVEN_COMPLETE)
    if not GOLDEN_PATH.exists():
        pytest.fail(f"golden fixture missing: {GOLDEN_PATH}")
    committed = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert produced == committed, (
        "worker snapshot drifted from the committed golden fixture; "
        f"regenerate {GOLDEN_PATH} and re-run the vitest cross-language check"
    )


def test_golden_summary_matches_the_committed_fixture() -> None:
    """Same contract, for the summary the page refuses to boot without.

    A description of the payload is not enough here: the page rejected a summary
    for a missing key while every Python assertion about that summary passed.
    Both sides now agree on one artifact instead of on two descriptions.
    """
    produced = build_summary(
        (_contract_view(),),
        timestamp="2026-08-05T00:00:00+00:00",
        run_dir="/tmp/csa-run",
    )
    if not GOLDEN_SUMMARY_PATH.exists():
        pytest.fail(f"golden fixture missing: {GOLDEN_SUMMARY_PATH}")
    committed = json.loads(GOLDEN_SUMMARY_PATH.read_text(encoding="utf-8"))
    assert produced == committed, (
        "csa summary drifted from the committed golden fixture; "
        f"regenerate {GOLDEN_SUMMARY_PATH} and re-run the vitest cross-language check"
    )
