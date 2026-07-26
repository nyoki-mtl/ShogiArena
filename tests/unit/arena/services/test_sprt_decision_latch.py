"""SPRT の停止判定をラッチする（task 0052 / review H1）。

停止規則は「停止した時点の標本」の関数でなければならない。
決着後に到着した in-flight 局を標本へ足すと、一度確定した decision が continue へ戻り、
`sprt-finished` で止めた run が terminal reason だけ `incomplete` になる。

実 run で観測した 30W/11D/4L（45局で accept_h1）に、後着の1敗を足すと
continue へ戻ることを回帰として固定する。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from shogiarena._core.contexts.game_session.application.completion.session_service import (
    TournamentSessionCompletionService,
)
from shogiarena._core.contexts.game_session.application.sprt_service import Sprt, SprtDecision
from shogiarena._core.contexts.game_session.domain.run_health import (
    RunTerminationReason,
    resolve_termination_reason,
)
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.session_hooks import SessionStopController

# 実 run（.sandbox の SPRT smoke）で decision に到達した標本。
_OBSERVED_WINS = 30
_OBSERVED_DRAWS = 11
_OBSERVED_LOSSES = 4


def _sprt() -> Sprt:
    """実 run（.sandbox の SPRT smoke）と同じ検定パラメータ。

    30W/11D/4L で LLR=3.369（bound=2.944）→ accept_h1、
    後着の1敗を足すと LLR=2.925 → continue へ戻る。
    """
    return Sprt(elo0=0.0, elo1=20.0, alpha=0.05, beta=0.05)


def _feed(sprt: Sprt, *, wins: int, draws: int, losses: int) -> None:
    """decision へ到達する順序に依存しないよう、負け→引き分け→勝ちの順で入れる。"""
    for _ in range(losses):
        sprt.add_game_result(GameResult.BLACK_WIN)
    for _ in range(draws):
        sprt.add_game_result(GameResult.DRAW_BY_REPETITION)
    for _ in range(wins):
        sprt.add_game_result(GameResult.WHITE_WIN)


def test_the_observed_sample_reaches_a_decision() -> None:
    """前提の確認: 実 run と同じ標本は accept_h1 に到達する。"""

    sprt = _sprt()
    _feed(sprt, wins=_OBSERVED_WINS, draws=_OBSERVED_DRAWS, losses=_OBSERVED_LOSSES)

    assert sprt.is_finished()
    assert sprt.get_status().decision is SprtDecision.ACCEPT_H1


def test_a_late_game_would_revoke_the_decision_without_the_latch() -> None:
    """ラッチしなければ後着の1敗で decision が continue へ戻る（欠陥の再現）。"""

    sprt = _sprt()
    _feed(sprt, wins=_OBSERVED_WINS, draws=_OBSERVED_DRAWS, losses=_OBSERVED_LOSSES)
    decided_llr = sprt.get_status().llr

    # ラッチせずに後着局を入れる。
    sprt.add_game_result(GameResult.BLACK_WIN)

    assert sprt.get_status().decision is SprtDecision.CONTINUE
    assert sprt.get_status().llr < decided_llr
    assert not sprt.is_finished()


def test_the_latch_keeps_the_decision_when_late_games_arrive() -> None:
    """ラッチ後の後着局は標本へ入らず、decision も LLR も動かない。"""

    sprt = _sprt()
    _feed(sprt, wins=_OBSERVED_WINS, draws=_OBSERVED_DRAWS, losses=_OBSERVED_LOSSES)
    decided = sprt.latch_decision()

    for _ in range(3):
        sprt.add_game_result(GameResult.BLACK_WIN)

    status = sprt.get_status()
    assert status.decision is SprtDecision.ACCEPT_H1
    assert status.llr == decided.llr
    assert status.games_played == decided.games_played
    assert status.losses == _OBSERVED_LOSSES
    assert sprt.is_finished()
    # 後着局は消さずに数える（DB には残るので、artifact 側と件数を突き合わせられる）。
    assert status.late_games == 3
    assert sprt.late_games == 3


def test_the_latch_is_idempotent() -> None:
    sprt = _sprt()
    _feed(sprt, wins=_OBSERVED_WINS, draws=_OBSERVED_DRAWS, losses=_OBSERVED_LOSSES)

    first = sprt.latch_decision()
    second = sprt.latch_decision()

    assert first.llr == second.llr
    assert first.games_played == second.games_played


def test_the_latch_survives_a_snapshot_round_trip() -> None:
    """resume してもラッチと後着件数が失われないこと。"""

    sprt = _sprt()
    _feed(sprt, wins=_OBSERVED_WINS, draws=_OBSERVED_DRAWS, losses=_OBSERVED_LOSSES)
    sprt.latch_decision()
    sprt.add_game_result(GameResult.BLACK_WIN)

    restored = Sprt.from_snapshot(sprt.to_snapshot())

    assert restored.is_decision_latched
    assert restored.late_games == 1
    assert restored.get_status().decision is SprtDecision.ACCEPT_H1
    # 復元後にさらに後着しても標本は締まったまま。
    restored.add_game_result(GameResult.BLACK_WIN)
    assert restored.get_status().decision is SprtDecision.ACCEPT_H1
    assert restored.late_games == 2


def test_an_unlatched_snapshot_stays_unlatched() -> None:
    """まだ止めていない run を resume してラッチが勝手に付かないこと。

    ``min_games`` に届く前に一時的に bound を越えている状態を、
    実際に停止した状態と取り違えると、以降の局をすべて捨ててしまう。
    """

    sprt = _sprt()
    _feed(sprt, wins=_OBSERVED_WINS, draws=_OBSERVED_DRAWS, losses=_OBSERVED_LOSSES)

    restored = Sprt.from_snapshot(sprt.to_snapshot())

    assert not restored.is_decision_latched
    restored.add_game_result(GameResult.BLACK_WIN)
    assert restored.get_status().decision is SprtDecision.CONTINUE


def test_a_pre_1_1_0_snapshot_without_the_latch_keys_loads() -> None:
    """古い state.json（新 key なし）でも resume できること。"""

    sprt = _sprt()
    _feed(sprt, wins=2, draws=1, losses=1)
    snapshot = dict(sprt.to_snapshot())
    del snapshot["is_decision_latched"]
    del snapshot["late_games"]

    restored = Sprt.from_snapshot(cast(Any, snapshot))

    assert not restored.is_decision_latched
    assert restored.late_games == 0
    assert restored.games_played == 4


# ---------------------------------------------------------------------------
# 停止要求とラッチの結線、および terminal reason への影響
# ---------------------------------------------------------------------------


def _state_context(sprt: Sprt, *, min_games: int, controller: SessionStopController) -> Any:
    return SimpleNamespace(
        sprt_service=sprt,
        sprt_pair=("tested", "base"),
        sprt_min_games=min_games,
        stop_controller=controller,
        completed_game_ids=set(),
        completed_game_summaries={},
        consecutive_invalid_timeouts_by_origin={},
        invalid_timeouts_by_origin={},
        is_dashboard_enabled=False,
        save_run_state=lambda: None,
    )


def _game_spec(game_id: str) -> Any:
    return SimpleNamespace(game_id=game_id, black_engine="base", white_engine="tested")


def test_requesting_the_sprt_stop_also_closes_the_sample() -> None:
    """`sprt-finished` を要求した時点で標本が締まること（結線の確認）。"""

    sprt = _sprt()
    _feed(sprt, wins=_OBSERVED_WINS, draws=_OBSERVED_DRAWS, losses=_OBSERVED_LOSSES - 1)
    controller = SessionStopController()
    context = _state_context(sprt, min_games=10, controller=controller)
    service = TournamentSessionCompletionService()

    # この1局で decision へ到達する。
    service.commit_completion_state(
        context,
        _game_spec("g0045"),
        summary=cast(Any, object()),
        result=GameResult.BLACK_WIN,
    )

    assert controller.reason == "sprt-finished"
    assert sprt.is_decision_latched


def test_the_sample_stays_open_below_min_games() -> None:
    """`min_games` に届いていなければ止めもラッチもしないこと。"""

    sprt = _sprt()
    _feed(sprt, wins=_OBSERVED_WINS, draws=_OBSERVED_DRAWS, losses=_OBSERVED_LOSSES - 1)
    controller = SessionStopController()
    context = _state_context(sprt, min_games=10_000, controller=controller)
    service = TournamentSessionCompletionService()

    service.commit_completion_state(
        context,
        _game_spec("g0045"),
        summary=cast(Any, object()),
        result=GameResult.BLACK_WIN,
    )

    assert controller.reason is None
    assert not sprt.is_decision_latched
    # 標本は開いたまま。以降の局も検定へ入る。
    sprt.add_game_result(GameResult.WHITE_WIN)
    assert sprt.games_played == _OBSERVED_WINS + _OBSERVED_DRAWS + _OBSERVED_LOSSES + 1


@pytest.mark.parametrize("late_losses", [1, 3])
def test_a_stopped_run_stays_sprt_finished_after_late_games(late_losses: int) -> None:
    """後着局が到着しても terminal reason が `incomplete` へ落ちないこと。"""

    sprt = _sprt()
    _feed(sprt, wins=_OBSERVED_WINS, draws=_OBSERVED_DRAWS, losses=_OBSERVED_LOSSES - 1)
    controller = SessionStopController()
    context = _state_context(sprt, min_games=10, controller=controller)
    service = TournamentSessionCompletionService()
    service.commit_completion_state(
        context,
        _game_spec("g0045"),
        summary=cast(Any, object()),
        result=GameResult.BLACK_WIN,
    )

    for index in range(late_losses):
        service.commit_completion_state(
            context,
            _game_spec(f"g{46 + index:04d}"),
            summary=cast(Any, object()),
            result=GameResult.BLACK_WIN,
        )

    has_decision = sprt.is_finished() and sprt.get_status().decision is not SprtDecision.CONTINUE
    reason = resolve_termination_reason(
        stop_reason=controller.reason,
        is_schedule_complete=False,
        has_valid_sprt_decision=has_decision,
    )

    assert reason is RunTerminationReason.SPRT_FINISHED
    assert sprt.late_games == late_losses


# ---------------------------------------------------------------------------
# resume 時の game.db との突き合わせ（review 第3次 H1 / 第4次 H2）
# ---------------------------------------------------------------------------


def _reconcile(sprt: Sprt, *, decisive_games: int, tested: str = "tested") -> None:
    """``_reconcile_sprt_with_completed_games`` を直接呼ぶ（resume 全体は integration 側）。"""

    from shogiarena._core.contexts.tournament.application.session.state_store import (
        TournamentSessionStateStore,
    )

    completed = [
        {
            "result": GameResult.WHITE_WIN,
            "black_player": "base",
            "white_player": tested,
            "game_name": f"g{index + 1:04d}-x",
        }
        for index in range(decisive_games)
    ]
    ctx = cast(Any, SimpleNamespace(state=SimpleNamespace(sprt=sprt, sprt_pair=(tested, "base"))))
    TournamentSessionStateStore()._reconcile_sprt_with_completed_games(
        ctx, cast(Any, sprt), completed_games=cast(Any, completed)
    )


def test_resume_accepts_a_latched_run_whose_sample_excludes_late_games() -> None:
    """ラッチ後の run を resume できること。

    後着局は意図して標本から除いて ``late_games`` に数える。突き合わせが標本数だけを
    見ていると、正常に早期終了した run も、ラッチ後 finalize 前に落ちた run も復旧不能になる。
    """

    sprt = _sprt()
    _feed(sprt, wins=_OBSERVED_WINS, draws=_OBSERVED_DRAWS, losses=_OBSERVED_LOSSES)
    sprt.latch_decision()
    sprt.add_game_result(GameResult.BLACK_WIN)  # 後着局

    # 実 run と同じ形: 標本 45、後着 1、DB の decisive 局は 46。
    assert (sprt.games_played, sprt.late_games) == (45, 1)

    _reconcile(sprt, decisive_games=46)

    assert (sprt.games_played, sprt.late_games) == (45, 1)


def test_resume_replays_games_committed_after_the_last_state_save() -> None:
    """DB へ commit した後 state 保存の前に落ちた分を replay すること（DB が正本）。"""

    sprt = _sprt()
    _feed(sprt, wins=2, draws=1, losses=1)
    assert sprt.games_played == 4

    # DB には 6 局ある（state.json は 4 局分で止まっている）。
    _reconcile(sprt, decisive_games=6)

    assert sprt.games_played == 6
    # replay した 2 局は tested の勝ち。
    assert sprt.wins == 4


def test_resume_replays_late_games_into_the_late_counter_when_latched() -> None:
    """ラッチ済みなら、replay 分は標本ではなく後着として数えること。"""

    sprt = _sprt()
    _feed(sprt, wins=_OBSERVED_WINS, draws=_OBSERVED_DRAWS, losses=_OBSERVED_LOSSES)
    sprt.latch_decision()

    _reconcile(sprt, decisive_games=47)

    assert sprt.games_played == 45
    assert sprt.late_games == 2
    assert sprt.get_status().decision is SprtDecision.ACCEPT_H1


def test_resume_rejects_an_sprt_state_ahead_of_the_db() -> None:
    """DB より進んだ SPRT は追いつかせようがないので fail closed にすること。"""

    from shogiarena._core.contexts.tournament.application.session.state_store import (
        TournamentSessionStateStore,
    )

    sprt = _sprt()
    _feed(sprt, wins=5, draws=2, losses=2)
    completed = [
        {"result": GameResult.WHITE_WIN, "black_player": "base", "white_player": "tested", "game_name": "g0001-x"}
    ]
    ctx = cast(Any, SimpleNamespace(state=SimpleNamespace(sprt=sprt, sprt_pair=("tested", "base"))))

    with pytest.raises(RuntimeError, match="ahead of game.db"):
        TournamentSessionStateStore()._reconcile_sprt_with_completed_games(
            ctx, cast(Any, sprt), completed_games=cast(Any, completed)
        )


def test_resume_skips_reconciliation_without_a_tested_pair() -> None:
    """検定対象ペアが決まらない構成では、live でも標本へ入らないので突き合わせない。"""

    from shogiarena._core.contexts.tournament.application.session.state_store import (
        TournamentSessionStateStore,
    )

    sprt = _sprt()
    completed = [{"result": GameResult.WHITE_WIN, "black_player": "a", "white_player": "b", "game_name": "g0001-x"}]
    ctx = cast(Any, SimpleNamespace(state=SimpleNamespace(sprt=sprt, sprt_pair=None)))

    TournamentSessionStateStore()._reconcile_sprt_with_completed_games(
        ctx, cast(Any, sprt), completed_games=cast(Any, completed)
    )

    assert sprt.games_played == 0


# ---------------------------------------------------------------------------
# replay 中の decision 到達（review 第5次 H2）
# ---------------------------------------------------------------------------


def _db_game(index: int, result: GameResult, *, slot: int | None = None, tested_is_black: bool = False) -> Any:
    """``completed_games`` の 1 行。tested が黒か白かで盤面視点の結果が決まる。"""
    round_index = index if slot is None else slot * 2
    return {
        "result": result,
        "black_player": "tested" if tested_is_black else "base",
        "white_player": "base" if tested_is_black else "tested",
        # round_index_from_game_name は 1-based を 0-based へ直すので +1 する。
        "game_name": f"g{round_index + 1:04d}-x",
        "initial_sfen": "startpos" if slot is None else f"sfen-{slot}",
    }


def _reconcile_db(sprt: Sprt, completed_games: list[Any], *, min_games: int = 10) -> None:
    from shogiarena._core.contexts.tournament.application.session.state_store import (
        TournamentSessionStateStore,
    )

    ctx = cast(
        Any,
        SimpleNamespace(state=SimpleNamespace(sprt=sprt, sprt_pair=("tested", "base"), sprt_min_games=min_games)),
    )
    TournamentSessionStateStore()._reconcile_sprt_with_completed_games(
        ctx, cast(Any, sprt), completed_games=cast(Any, completed_games)
    )


def _filler(count: int) -> list[Any]:
    """既に反映済みの局。``decisive[already:]`` で切り落とされるだけの詰め物。"""
    return [_db_game(index, GameResult.WHITE_WIN) for index in range(count)]


def test_replay_latches_at_the_game_that_crosses_the_bound() -> None:
    """suffix の途中で decision へ到達したら、その場でラッチすること。

    全部入れてから評価すると、到達後の局まで標本に入り decision を continue へ戻せる。
    """

    sprt = _sprt()
    # 42局（llr=2.860、bound=2.944）で手前。次の1勝で到達する。
    _feed(sprt, wins=27, draws=11, losses=4)
    assert sprt.games_played == 42
    assert not sprt.is_finished()

    # DB には 44 局: 43局目が tested の勝ち（到達）、44局目が負け（本来は後着）。
    suffix = [_db_game(42, GameResult.WHITE_WIN), _db_game(43, GameResult.BLACK_WIN)]
    _reconcile_db(sprt, [*_filler(42), *suffix])

    assert sprt.games_played == 43
    assert sprt.late_games == 1
    assert sprt.get_status().decision is SprtDecision.ACCEPT_H1


def test_replay_does_not_latch_below_min_games() -> None:
    """`min_games` に届かないうちは、途中で bound を越えてもラッチしないこと。"""

    sprt = _sprt()
    _feed(sprt, wins=27, draws=11, losses=4)

    suffix = [_db_game(42, GameResult.WHITE_WIN), _db_game(43, GameResult.BLACK_WIN)]
    _reconcile_db(sprt, [*_filler(42), *suffix], min_games=10_000)

    assert sprt.games_played == 44
    assert sprt.late_games == 0
    assert not sprt.is_decision_latched


def _pentanomial_sprt() -> Sprt:
    from shogiarena._core.shared.kernel.sprt_models import SPRT_MODEL_GSPRT_PENTANOMIAL

    return Sprt(elo0=0.0, elo1=20.0, alpha=0.05, beta=0.05, model=SPRT_MODEL_GSPRT_PENTANOMIAL, min_pairs=30)


def _feed_pairs(sprt: Sprt, *, ww: int = 0, dd: int = 0, ll: int = 0, start: int = 0) -> int:
    """完成した pair を投入する。返り値は次に使える pair slot。"""
    slot = start
    for score, count in ((1.0, ww), (0.5, dd), (0.0, ll)):
        for _ in range(count):
            for is_black in (True, False):
                sprt.add_game_observation(
                    sfen=f"sfen-{slot}", pair_slot=slot, is_tested_black=is_black, tested_score=score
                )
            slot += 1
    return slot


def test_pentanomial_replay_latches_at_the_pair_that_crosses_the_bound() -> None:
    """pentanomial でも、到達した pair でラッチして以降を late にすること。

    第4次の replay は pending pair の復元だけを見ており、decision を跨ぐ suffix では不正だった。
    このコミット以前は、この分岐を直接通す回帰も無かった。
    """

    sprt = _pentanomial_sprt()
    next_slot = _feed_pairs(sprt, ww=17, dd=12, ll=1)
    assert sprt.games_played == 60
    assert not sprt.is_finished()

    ww_slot, ll_slot = next_slot, next_slot + 1
    suffix = [
        # WW: tested が両色で勝つ → ここで到達する。
        _db_game(0, GameResult.BLACK_WIN, slot=ww_slot, tested_is_black=True),
        _db_game(0, GameResult.WHITE_WIN, slot=ww_slot, tested_is_black=False),
        # LL: tested が両色で負ける → 本来は後着。
        _db_game(0, GameResult.WHITE_WIN, slot=ll_slot, tested_is_black=True),
        _db_game(0, GameResult.BLACK_WIN, slot=ll_slot, tested_is_black=False),
    ]
    _reconcile_db(sprt, [*_filler(60), *suffix])

    assert sprt.is_decision_latched, "the WW pair must latch the decision"
    assert sprt.get_status().decision is not SprtDecision.CONTINUE
    # LL pair は標本へ入らず後着として数える。
    assert sprt.late_games == 2
    assert sprt.games_played == 62


def test_a_breaker_stop_wins_over_an_earlier_sprt_stop_on_the_live_path() -> None:
    """live 経路でも、安全停止が SPRT の正常終了より強いこと（review 第5次 M3）。

    resume 側は SPRT → breaker の順に評価するので、後勝ちにすると
    resume したかどうかだけで `clean` と `failed` が入れ替わる。
    """

    from shogiarena._core.shared.kernel.timeout_attribution import TimeoutOrigin
    from shogiarena._core.shared.kernel.timeout_breaker import TIMEOUT_BREAKER_POLICIES

    origin = TimeoutOrigin.UNKNOWN.value
    policy = TIMEOUT_BREAKER_POLICIES[origin]

    sprt = _sprt()
    _feed(sprt, wins=_OBSERVED_WINS, draws=_OBSERVED_DRAWS, losses=_OBSERVED_LOSSES - 1)
    controller = SessionStopController()
    context = _state_context(sprt, min_games=10, controller=controller)

    service = TournamentSessionCompletionService()
    # 1局目で SPRT が決着する。
    service.commit_completion_state(
        context, _game_spec("g0045"), summary=cast(Any, object()), result=GameResult.BLACK_WIN
    )
    assert controller.reason == "sprt-finished"

    # 続けて無効 timeout が閾値へ達する（決着後に到着した局でも breaker は数える）。
    context.consecutive_invalid_timeouts_by_origin[origin] = policy.consecutive_limit - 1
    context.invalid_timeouts_by_origin[origin] = policy.consecutive_limit - 1
    service.commit_completion_state(
        context,
        _game_spec("g0046"),
        summary=cast(Any, object()),
        result=GameResult.ERROR,
        invalid_timeout_origin=origin,
    )

    assert controller.reason == policy.termination_reason
