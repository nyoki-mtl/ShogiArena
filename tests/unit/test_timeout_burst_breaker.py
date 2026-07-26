"""origin 別 timeout breaker（task 0047、0052 で policy 入力へ変更）。

breaker は「結果がたまたま ``ERROR`` か」ではなく分類済みの timeout policy を入力にする。
origin ごとに独立した counter を持ち、閾値未満なら run は継続する（decisions.md Decision 6）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.game_session.application.completion.session_service import (
    TournamentSessionCompletionService,
)
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.timeout_breaker import TIMEOUT_BREAKER_POLICIES

_STALL = "orchestrator_stall"
_UNKNOWN = "unknown"
_TRANSPORT = "transport_timeout"


class _StopController:
    def __init__(self) -> None:
        self.reasons: list[str | None] = []

    def request_stop(self, *, reason: str | None = None) -> None:
        self.reasons.append(reason)


def _context(*, completed: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        consecutive_invalid_timeouts_by_origin={},
        invalid_timeouts_by_origin={},
        completed_game_ids={f"g{index}" for index in range(completed)},
        stop_controller=_StopController(),
    )


def _feed(
    service: TournamentSessionCompletionService,
    ctx: SimpleNamespace,
    count: int,
    *,
    origin: str = _STALL,
    result: GameResult = GameResult.ERROR,
) -> None:
    for _ in range(count):
        service._apply_timeout_burst_breaker(  # type: ignore[arg-type]  # noqa: SLF001
            ctx, result=result, invalid_timeout_origin=origin
        )


@pytest.mark.parametrize("origin", [_STALL, _UNKNOWN, _TRANSPORT])
def test_consecutive_limit_requests_stop_with_the_origin_specific_reason(origin: str) -> None:
    service = TournamentSessionCompletionService()
    ctx = _context()
    policy = TIMEOUT_BREAKER_POLICIES[origin]

    _feed(service, ctx, policy.consecutive_limit - 1, origin=origin)
    assert ctx.stop_controller.reasons == []  # below threshold: the run keeps going

    _feed(service, ctx, 1, origin=origin)
    assert ctx.stop_controller.reasons == [policy.termination_reason]


def test_a_valid_result_resets_every_origin_counter() -> None:
    service = TournamentSessionCompletionService()
    ctx = _context()
    limit = TIMEOUT_BREAKER_POLICIES[_STALL].consecutive_limit

    _feed(service, ctx, limit - 1)
    service._apply_timeout_burst_breaker(ctx, result=GameResult.BLACK_WIN, invalid_timeout_origin=None)  # noqa: SLF001
    assert ctx.consecutive_invalid_timeouts_by_origin == {}

    _feed(service, ctx, limit - 1)
    assert ctx.stop_controller.reasons == []  # streak had to restart


def test_origins_have_independent_counters() -> None:
    """別 origin が交互に出ても、片方の counter が他方で進まない。"""

    service = TournamentSessionCompletionService()
    ctx = _context(completed=1000)

    for _ in range(10):
        _feed(service, ctx, 1, origin=_STALL)
        _feed(service, ctx, 1, origin=_UNKNOWN)

    assert ctx.stop_controller.reasons == []
    assert ctx.consecutive_invalid_timeouts_by_origin == {_UNKNOWN: 1}


def test_engine_deadline_timeout_does_not_count_toward_the_breaker() -> None:
    service = TournamentSessionCompletionService()
    ctx = _context()

    _feed(service, ctx, 10, origin="engine_deadline")
    assert ctx.stop_controller.reasons == []
    assert ctx.consecutive_invalid_timeouts_by_origin == {}


def test_unattributed_timeout_does_not_count_toward_the_breaker() -> None:
    service = TournamentSessionCompletionService()
    ctx = _context()

    _feed(service, ctx, 10, origin="unattributed")
    assert ctx.stop_controller.reasons == []


def test_ratio_limit_stops_a_long_run_with_scattered_unknown_timeouts() -> None:
    """連続していなくても、除外率が無視できない水準になれば停止する。"""

    service = TournamentSessionCompletionService()
    policy = TIMEOUT_BREAKER_POLICIES[_UNKNOWN]
    assert policy.ratio_limit is not None
    ctx = _context(completed=100)

    for _ in range(policy.ratio_min_games):
        _feed(service, ctx, 1, origin=_UNKNOWN)
        service._apply_timeout_burst_breaker(ctx, result=GameResult.BLACK_WIN, invalid_timeout_origin=None)  # noqa: SLF001

    # 5/100 = 5% > 1%
    assert ctx.stop_controller.reasons == [policy.termination_reason]


def test_ratio_limit_does_not_stop_below_the_minimum_sample() -> None:
    service = TournamentSessionCompletionService()
    ctx = _context(completed=100)
    policy = TIMEOUT_BREAKER_POLICIES[_UNKNOWN]

    for _ in range(policy.ratio_min_games - 1):
        _feed(service, ctx, 1, origin=_UNKNOWN)
        service._apply_timeout_burst_breaker(ctx, result=GameResult.BLACK_WIN, invalid_timeout_origin=None)  # noqa: SLF001

    assert ctx.stop_controller.reasons == []


def test_scattered_unknown_timeouts_below_the_ratio_keep_the_run_going() -> None:
    """閾値未満の ``unknown`` は標本から除外するだけで run を止めない。"""

    service = TournamentSessionCompletionService()
    ctx = _context(completed=5000)

    for _ in range(20):
        _feed(service, ctx, 1, origin=_UNKNOWN)
        service._apply_timeout_burst_breaker(ctx, result=GameResult.BLACK_WIN, invalid_timeout_origin=None)  # noqa: SLF001

    assert ctx.stop_controller.reasons == []
    assert ctx.invalid_timeouts_by_origin == {_UNKNOWN: 20}


def test_counters_survive_a_completion_context_rebuilt_per_game() -> None:
    """completion context は局ごとに組み直される。counter が run-scoped でないと閾値へ届かない。

    review 指摘: context 側に既定値を置くと毎回 `{}` に戻り、production run では
    連続回数・比率のどちらの閾値にも到達しなかった（task 0052）。
    """

    service = TournamentSessionCompletionService()
    stop_controller = _StopController()
    # runner state が持つ run-scoped な mapping。
    consecutive: dict[str, int] = {}
    totals: dict[str, int] = {}
    completed: set[str] = set()
    limit = TIMEOUT_BREAKER_POLICIES[_UNKNOWN].consecutive_limit

    for index in range(limit):
        # 局ごとに context を作り直す（TournamentRunner._build_completion_runtime_context 相当）。
        ctx = SimpleNamespace(
            consecutive_invalid_timeouts_by_origin=consecutive,
            invalid_timeouts_by_origin=totals,
            completed_game_ids=completed,
            stop_controller=stop_controller,
        )
        completed.add(f"g{index}")
        service._apply_timeout_burst_breaker(  # type: ignore[arg-type]  # noqa: SLF001
            ctx, result=GameResult.ERROR, invalid_timeout_origin=_UNKNOWN
        )

    assert consecutive == {_UNKNOWN: limit}
    assert totals == {_UNKNOWN: limit}
    assert stop_controller.reasons == [TIMEOUT_BREAKER_POLICIES[_UNKNOWN].termination_reason]


def test_completion_state_context_requires_run_scoped_counters() -> None:
    """既定値で握りつぶされないよう、counter は必ず注入させる。"""

    import inspect

    from shogiarena._core.contexts.game_session.ports.completion_runtime import CompletionStateContext

    parameters = inspect.signature(CompletionStateContext).parameters
    for name in ("consecutive_invalid_timeouts_by_origin", "invalid_timeouts_by_origin"):
        assert parameters[name].default is inspect.Parameter.empty, (
            f"{name} must not have a default; the context is rebuilt per game"
        )


def test_orchestrator_stall_also_has_a_ratio_limit() -> None:
    """停滞が確定していても連続では止まらないケースを比率で拾う（Decision 16 の実測）。

    shadow 計測では ``orchestrator_stall`` の最大連続が 2 までしか観測されず、
    連続閾値 5 だけでは継続的な停滞でも run が止まらなかった。
    """

    service = TournamentSessionCompletionService()
    policy = TIMEOUT_BREAKER_POLICIES[_STALL]
    assert policy.ratio_limit is not None
    ctx = _context(completed=100)

    for _ in range(policy.ratio_min_games):
        _feed(service, ctx, 1, origin=_STALL)
        service._apply_timeout_burst_breaker(ctx, result=GameResult.BLACK_WIN, invalid_timeout_origin=None)  # noqa: SLF001

    # 5/100 = 5% > 1%。連続は 1 のままでも比率で止まる。
    assert ctx.stop_controller.reasons == [policy.termination_reason]


def test_a_light_stall_rate_keeps_the_run_going() -> None:
    """shadow 計測の軽微な停滞条件（0.33%）では止めないこと。"""

    service = TournamentSessionCompletionService()
    ctx = _context(completed=300)

    _feed(service, ctx, 1, origin=_STALL)
    service._apply_timeout_burst_breaker(ctx, result=GameResult.BLACK_WIN, invalid_timeout_origin=None)  # noqa: SLF001

    assert ctx.stop_controller.reasons == []
