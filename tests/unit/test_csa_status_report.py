"""`shogiarena csa status` の描画と局面再生（0067）。"""

from __future__ import annotations

from pathlib import Path

from shogiarena._core.contexts.csa_watch.adapters.rsshogi_replay import RsshogiBoardReplay
from shogiarena._core.contexts.csa_watch.application.run_watcher import RunView
from shogiarena._core.contexts.csa_watch.application.status_report import (
    format_clock_ms,
    format_duration_ms,
    render_run,
    render_status,
)
from shogiarena._core.contexts.csa_watch.domain.run_state import (
    AlertEntry,
    GameState,
    LogHealth,
    PendingSearch,
    RunState,
)

from .test_csa_event_fold import (
    BUOY_HANDICAP,
    ENGINE_DEATH,
    EVEN_COMPLETE,
    ILLEGAL_MOVE,
    fold_fixture,
)

NOW_MS = 1_800_000_100_000


def _view(state: RunState, *, has_partial_line: bool = False) -> RunView:
    return RunView(worker_idx=0, state=state, path=Path("events.jsonl"), has_partial_line=has_partial_line)


class TestFormatting:
    def test_clock_uses_minutes_and_seconds(self) -> None:
        assert format_clock_ms(79_000) == "1:19"
        assert format_clock_ms(262_000) == "4:22"

    def test_clock_never_shows_a_negative_value(self) -> None:
        assert format_clock_ms(-5_000) == "0:00"

    def test_clock_falls_back_when_unknown(self) -> None:
        assert format_clock_ms(None) == "--:--"

    def test_long_clocks_gain_an_hour_field(self) -> None:
        assert format_clock_ms(3_723_000) == "1:02:03"

    def test_duration_is_coarse(self) -> None:
        assert format_duration_ms(45_000) == "45s"
        assert format_duration_ms(2_400_000) == "40m 00s"
        assert format_duration_ms(7_200_000) == "2h 00m"


class TestRendering:
    def test_no_runs_says_so_with_the_directory(self) -> None:
        assert "under /logs" in render_status((), log_dir="/logs")

    def test_phase_dwell_time_is_rendered(self) -> None:
        state = RunState(run_id="r", phase="waiting_pairing", phase_since_ts=NOW_MS - 2_400_000)
        assert "waiting_pairing  (40m 00s)" in render_run(_view(state), now_ms=NOW_MS)

    def test_an_outstanding_deadline_counts_down(self) -> None:
        game = GameState(game_id="g", pending=PendingSearch(kind="go", ply=8, deadline_ts=NOW_MS + 4_200))
        state = RunState(run_id="r", games=(game,), current_game_id="g")
        assert "4.2s left (go)" in render_run(_view(state), now_ms=NOW_MS)

    def test_an_exceeded_deadline_is_stated_plainly(self) -> None:
        game = GameState(game_id="g", pending=PendingSearch(kind="go", ply=8, deadline_ts=NOW_MS - 12_000))
        state = RunState(run_id="r", games=(game,), current_game_id="g")
        assert "OVERDUE by 12s" in render_run(_view(state), now_ms=NOW_MS)

    def test_errors_are_listed_above_warnings(self) -> None:
        state = RunState(
            run_id="r",
            alerts=(
                AlertEntry(seq=1, level="warn", code="engine_restarted"),
                AlertEntry(seq=2, level="error", code="engine_dead"),
            ),
        )
        rendered = render_run(_view(state), now_ms=NOW_MS)
        assert rendered.index("engine_dead") < rendered.index("engine_restarted")

    def test_log_health_is_reported_field_by_field(self) -> None:
        state = RunState(
            run_id="r",
            health=LogHealth(missing_seq=2, malformed=1, invalid_lines=3, unknown_types={"weather": 4}),
        )
        rendered = render_run(_view(state, has_partial_line=True), now_ms=NOW_MS)
        assert "missing seq 2" in rendered
        assert "malformed 1" in rendered
        assert "unreadable lines 3" in rendered
        assert "unknown 'weather' x4" in rendered
        assert "trailing partial line" in rendered

    def test_a_clean_log_says_clean(self) -> None:
        assert "log         clean" in render_run(_view(RunState(run_id="r")), now_ms=NOW_MS)

    def test_fallback_moves_are_called_out(self) -> None:
        rendered = render_run(_view(fold_fixture(ENGINE_DEATH)), now_ms=NOW_MS)
        assert "1 move(s), latest ply 9" in rendered


class TestReplay:
    def test_a_complete_game_replays_move_for_move(self) -> None:
        game = fold_fixture(EVEN_COMPLETE).games[0]
        result = RsshogiBoardReplay().replay(game.initial_sfen, [move.usi for move in game.moves])
        assert result.is_complete
        assert len(result.plies) == game.current_ply
        assert result.final_sfen is not None
        assert all(entry.ki2 for entry in result.plies)

    def test_ki2_is_read_from_the_position_before_the_move(self) -> None:
        result = RsshogiBoardReplay().replay(
            "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1", ["7g7f"]
        )
        assert result.is_complete
        entry = result.plies[0]
        assert entry.sfen_before.split()[1] == "b"
        assert entry.sfen_after.split()[1] == "w"

    def test_a_handicap_start_replays_through_the_same_path(self) -> None:
        game = fold_fixture(BUOY_HANDICAP).games[0]
        result = RsshogiBoardReplay().replay(game.initial_sfen, [move.usi for move in game.moves])
        assert result.is_complete

    def test_an_illegal_move_stops_replay_and_says_where(self) -> None:
        game = fold_fixture(ILLEGAL_MOVE).games[0]
        result = RsshogiBoardReplay().replay(game.initial_sfen, [move.usi for move in game.moves])
        assert not result.is_complete
        assert result.failure is not None
        assert result.failure.ply == 5
        assert result.failure.reason

    def test_an_unreadable_initial_position_is_reported_not_raised(self) -> None:
        result = RsshogiBoardReplay().replay("not a sfen", ["7g7f"])
        assert result.failure is not None
        assert result.failure.ply == 0
