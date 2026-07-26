"""timeout breaker counter を resume で失わない（task 0052 / review M3）。

counter が resume でゼロへ戻ると、閾値の直前で pause / resume を繰り返すだけで
安全停止を実質的に回避できてしまう。state.json への永続化と、
古い state.json 向けの DB 再構築の双方を回帰として固定する。
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from shogiarena._core.contexts.game_session.application.completion.session_service import (
    TournamentSessionCompletionService,
)
from shogiarena._core.shared.kernel.boundary_parsers.runner_state_payloads.parsers import (
    parse_tournament_run_state_boundary,
)
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.timeout_attribution import TimeoutOrigin
from shogiarena._core.shared.kernel.timeout_breaker import (
    TIMEOUT_BREAKER_POLICIES,
    rebuild_timeout_breaker_counters,
)

_UNKNOWN = TimeoutOrigin.UNKNOWN.value
_STALL = TimeoutOrigin.ORCHESTRATOR_STALL.value


def _game(result: GameResult, origin: str | None = None) -> dict[str, object]:
    record: dict[str, object] = {"result": result}
    if origin is not None:
        record["timeout_origin"] = origin
    return record


# ---------------------------------------------------------------------------
# DB からの再構築（1.1.0 より前の state.json 向け fallback）
# ---------------------------------------------------------------------------


def test_rebuild_counts_invalid_timeouts_by_origin() -> None:
    games = [
        _game(GameResult.WHITE_WIN),
        _game(GameResult.ERROR, _UNKNOWN),
        _game(GameResult.ERROR, _UNKNOWN),
        _game(GameResult.ERROR, _STALL),
    ]

    totals, consecutive = rebuild_timeout_breaker_counters(games)

    assert totals == {_UNKNOWN: 2, _STALL: 1}
    # 直近は stall なので、unknown の連続はそこで切れている。
    assert consecutive == {_STALL: 1}


def test_rebuild_resets_the_consecutive_run_on_a_valid_result() -> None:
    """有効な結果を挟んだら連続数は戻る（run 中の breaker と同じ規則）。"""

    games = [
        _game(GameResult.ERROR, _UNKNOWN),
        _game(GameResult.ERROR, _UNKNOWN),
        _game(GameResult.DRAW_BY_REPETITION),
        _game(GameResult.ERROR, _UNKNOWN),
    ]

    totals, consecutive = rebuild_timeout_breaker_counters(games)

    assert totals == {_UNKNOWN: 3}
    assert consecutive == {_UNKNOWN: 1}


def test_rebuild_ignores_origins_outside_the_breaker_policy() -> None:
    """breaker 対象外の origin（engine_deadline 等）は数えない。"""

    games = [
        _game(GameResult.ERROR, TimeoutOrigin.ENGINE_DEADLINE.value),
        _game(GameResult.ERROR, _UNKNOWN),
    ]

    totals, _ = rebuild_timeout_breaker_counters(games)

    assert totals == {_UNKNOWN: 1}
    assert TimeoutOrigin.ENGINE_DEADLINE.value not in TIMEOUT_BREAKER_POLICIES


def test_rebuild_ignores_an_origin_on_a_non_error_result() -> None:
    """有効な結果に origin が残っていても無効 timeout として数えない。"""

    totals, consecutive = rebuild_timeout_breaker_counters([_game(GameResult.WHITE_WIN, _UNKNOWN)])

    assert totals == {}
    assert consecutive == {}


# ---------------------------------------------------------------------------
# state.json への往復
# ---------------------------------------------------------------------------


def _run_state_with_counters(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schedule_hash": "h",
        "resume_hash": "r",
        "total_games": 10,
        "completed_games_count": 5,
        "cancelled_game_ids": [],
        "cancelled_games": [],
        "original_total_games": 10,
        "game_display_order": {},
        "is_finished": False,
    }
    base.update(extra)
    return base


def test_the_counters_survive_the_state_json_boundary() -> None:
    payload = _run_state_with_counters(
        invalid_timeouts_by_origin={_UNKNOWN: 4},
        consecutive_invalid_timeouts_by_origin={_UNKNOWN: 2},
    )

    parsed = parse_tournament_run_state_boundary(payload, path="state.json")

    assert parsed.get("invalid_timeouts_by_origin") == {_UNKNOWN: 4}
    assert parsed.get("consecutive_invalid_timeouts_by_origin") == {_UNKNOWN: 2}


def test_a_pre_1_1_0_state_json_without_the_counters_still_parses() -> None:
    parsed = parse_tournament_run_state_boundary(_run_state_with_counters(), path="state.json")

    assert not parsed.get("invalid_timeouts_by_origin")
    assert not parsed.get("consecutive_invalid_timeouts_by_origin")


# ---------------------------------------------------------------------------
# resume 時の復元経路
# ---------------------------------------------------------------------------


def _restore(saved_state: dict[str, object], completed_games: list[dict[str, object]]) -> Any:
    from types import SimpleNamespace

    from shogiarena._core.contexts.tournament.application.session.state_store import (
        TournamentSessionStateStore,
    )

    state = SimpleNamespace(invalid_timeouts_by_origin={}, consecutive_invalid_timeouts_by_origin={})
    TournamentSessionStateStore._restore_timeout_breaker_counters(
        cast(Any, SimpleNamespace(state=state)),
        saved_state,
        completed_games=cast(Any, completed_games),
    )
    return state


def test_resume_restores_the_persisted_counters() -> None:
    """DB が state.json と一致していれば、保存した counter がそのまま戻ること。"""

    state = _restore(
        {
            "invalid_timeouts_by_origin": {_UNKNOWN: 7},
            "consecutive_invalid_timeouts_by_origin": {_UNKNOWN: 2},
            "completed_games_count": 1,
        },
        [_game(GameResult.WHITE_WIN)],
    )

    assert state.invalid_timeouts_by_origin == {_UNKNOWN: 7}
    assert state.consecutive_invalid_timeouts_by_origin == {_UNKNOWN: 2}


def test_resume_rebuilds_the_counters_when_the_state_json_predates_them() -> None:
    state = _restore(
        {},
        [
            _game(GameResult.ERROR, _UNKNOWN),
            _game(GameResult.ERROR, _UNKNOWN),
        ],
    )

    assert state.invalid_timeouts_by_origin == {_UNKNOWN: 2}
    assert state.consecutive_invalid_timeouts_by_origin == {_UNKNOWN: 2}


# --- DB commit 後 / state 保存前に落ちた場合の突き合わせ（review M3 続き） -------------


def test_resume_replays_games_committed_after_the_last_state_save() -> None:
    """DB へ commit した後、state 保存の前に落ちた分を数え直すこと。

    state.json だけを信じると counter が実際より小さいまま復元され、
    その差で安全停止の閾値を回避できてしまう。
    """

    state = _restore(
        {
            "invalid_timeouts_by_origin": {_UNKNOWN: 4},
            "consecutive_invalid_timeouts_by_origin": {_UNKNOWN: 4},
            "completed_games_count": 4,
        },
        [_game(GameResult.ERROR, _UNKNOWN)] * 5,
    )

    assert state.invalid_timeouts_by_origin == {_UNKNOWN: 5}
    assert state.consecutive_invalid_timeouts_by_origin == {_UNKNOWN: 5}


def test_resume_resets_the_consecutive_run_from_a_game_committed_after_the_last_state_save() -> None:
    """逆に、後続が有効な結果なら連続数は戻ること（誤停止を避ける）。"""

    state = _restore(
        {
            "invalid_timeouts_by_origin": {_UNKNOWN: 4},
            "consecutive_invalid_timeouts_by_origin": {_UNKNOWN: 4},
            "completed_games_count": 4,
        },
        [*([_game(GameResult.ERROR, _UNKNOWN)] * 4), _game(GameResult.WHITE_WIN)],
    )

    assert state.invalid_timeouts_by_origin == {_UNKNOWN: 4}
    assert state.consecutive_invalid_timeouts_by_origin == {}


def test_resume_rebuilds_when_the_state_json_has_no_completed_count() -> None:
    """起点が分からない state.json では DB から作り直すこと（二重計上を避ける）。"""

    state = _restore(
        {
            "invalid_timeouts_by_origin": {_UNKNOWN: 4},
            "consecutive_invalid_timeouts_by_origin": {_UNKNOWN: 4},
        },
        [_game(GameResult.ERROR, _UNKNOWN)] * 2,
    )

    assert state.invalid_timeouts_by_origin == {_UNKNOWN: 2}


def test_resume_rebuilds_when_the_db_is_behind_the_state_json() -> None:
    """DB が state.json より古い場合も、正本である DB から作り直すこと。"""

    state = _restore(
        {
            "invalid_timeouts_by_origin": {_UNKNOWN: 9},
            "consecutive_invalid_timeouts_by_origin": {_UNKNOWN: 9},
            "completed_games_count": 9,
        },
        [_game(GameResult.ERROR, _UNKNOWN)] * 2,
    )

    assert state.invalid_timeouts_by_origin == {_UNKNOWN: 2}


# ---------------------------------------------------------------------------
# 復元した counter が閾値判定に効くこと
# ---------------------------------------------------------------------------


class _StopController:
    def __init__(self) -> None:
        self.reason: str | None = None

    def request_stop(self, *, reason: str) -> None:
        if self.reason is None:
            self.reason = reason


def _context(controller: _StopController, *, totals: dict[str, int], consecutive: dict[str, int]) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        sprt_service=None,
        sprt_pair=None,
        sprt_min_games=0,
        stop_controller=controller,
        completed_game_ids=set(),
        completed_game_summaries={},
        consecutive_invalid_timeouts_by_origin=consecutive,
        invalid_timeouts_by_origin=totals,
        is_dashboard_enabled=False,
        save_run_state=lambda: None,
    )


@pytest.mark.parametrize("origin", [_UNKNOWN, _STALL])
def test_a_restored_consecutive_run_trips_the_breaker_on_the_next_game(origin: str) -> None:
    """resume 直後の1局で閾値へ達すること。counter を捨てていると到達しない。"""

    policy = TIMEOUT_BREAKER_POLICIES[origin]
    controller = _StopController()
    context = _context(
        controller,
        totals={origin: policy.consecutive_limit - 1},
        consecutive={origin: policy.consecutive_limit - 1},
    )
    service = TournamentSessionCompletionService()

    service.commit_completion_state(
        context,
        cast(Any, type("_Spec", (), {"game_id": "g0100", "black_engine": "a", "white_engine": "b"})()),
        summary=cast(Any, object()),
        result=GameResult.ERROR,
        invalid_timeout_origin=origin,
    )

    assert controller.reason == policy.termination_reason


def test_a_zeroed_counter_does_not_trip_the_breaker() -> None:
    """counter を失った状態（欠陥の再現）では同じ1局で止まらない。"""

    controller = _StopController()
    context = _context(controller, totals={}, consecutive={})
    service = TournamentSessionCompletionService()

    service.commit_completion_state(
        context,
        cast(Any, type("_Spec", (), {"game_id": "g0100", "black_engine": "a", "white_engine": "b"})()),
        summary=cast(Any, object()),
        result=GameResult.ERROR,
        invalid_timeout_origin=_UNKNOWN,
    )

    assert controller.reason is None
