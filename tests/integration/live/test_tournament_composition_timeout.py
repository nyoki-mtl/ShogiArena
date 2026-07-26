"""Production composition を通した timeout attribution（task 0052 / Decision 15）。

0047 の事故は「`TournamentRunner.run` が base の run を上書きしていたため watchdog が
起動していなかった」という **配線** の問題だった。`GameRunner` を直接組み立てて watchdog を
手で注入する test では、この種の配線漏れを検出できない。

ここでは public entrypoint（`shogiarena.tournament.build_tournament_runner`）から
default composition root を通し、実 subprocess の dummy USI engine で対局させる。

検証するのは次の点である。

- production 配線で watchdog が実際に起動し、`completion_status.json` に計測値が載ること。
- production 配線で timeout attribution が働き、engine 起因の時間切れが `engine_deadline` として
  DB と run-health artifact に現れること。
- engine の遅延と event loop 停滞が重なったとき、engine のせいにしないこと。
- ring からあふれて coverage を失ったとき、engine のせいにしないこと。
- 2局以上を並行させても、局ごとに独立した timeout 判定になること。
- cancellation でも terminal status が確定し、watchdog 計測値を伴うこと。

``timeout_reclassification`` の opt-in 差（tournament は有効、SPSA は無効）だけは
実 SPSA run ではなく orchestrator の配線で確認している。verification.md にその旨を残す。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sqlite3
import sys
import textwrap
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from shogiarena._core.contexts.match.application.runner_finalize_mixin import GameRunnerFinalizeMixin
from shogiarena._core.contexts.match.application.runner_loop_mixin import GameRunnerLoopMixin
from shogiarena.tournament import build_tournament_runner, create_run_storage, load_tournament_config

_ENGINE_SOURCE = """\
#!/usr/bin/env python3
import sys
import time

# 思考時間はスクリプトへ直接埋め込む。engine process の環境は spawner が組み立てるため、
# test 側の ``os.environ`` は必ずしも伝播しない。
DELAY_S = __DELAY_S__
# True なら、自分が白番の対局でだけ遅くなる。
# 1 run の中に「時間切れする局」と「しない局」を決定的に混在させるために使う。
SLOW_ONLY_AS_WHITE = __SLOW_ONLY_AS_WHITE__

# 直前に受け取った局面の手数。``go`` の時点で奇数なら自分は白番（後手）。
# 「最初に受け取った局面」では判定できない。開始時は両者とも moves なしの局面を受け取るため。
played_moves = 0
is_white = False
move_index = 0

# 手番ごとの合法手。初手から順に指す。時間切れさせない条件では、
# 反則手で即終局させないためにこの並びが必要になる。
BLACK_MOVES = ["7g7f", "2g2f", "6g6f", "5g5f"]
WHITE_MOVES = ["3c3d", "8c8d", "4c4d", "5c5d"]


def send(line):
    sys.stdout.write(line + "\\n")
    sys.stdout.flush()


def next_move():
    global move_index
    moves = WHITE_MOVES if is_white else BLACK_MOVES
    move = moves[move_index % len(moves)]
    move_index += 1
    return move


while True:
    raw = sys.stdin.readline()
    if not raw:
        break
    line = raw.strip()
    if not line:
        continue
    if line == "usi":
        send("id name CompositionMock")
        send("usiok")
    elif line == "isready":
        send("readyok")
    elif line == "quit":
        break
    elif line.startswith("position"):
        played_moves = len(line.split(" moves ", 1)[1].split()) if " moves " in line else 0
    elif line.startswith("go"):
        is_white = played_moves % 2 == 1
        if DELAY_S > 0.0 and (not SLOW_ONLY_AS_WHITE or is_white):
            time.sleep(DELAY_S)
        send("bestmove " + next_move())
    elif line == "stop":
        send("bestmove " + next_move())
"""


def _write_engine_script(engines_dir: Path, name: str, *, delay_s: float, slow_only_as_white: bool) -> Path:
    script = engines_dir / f"{name}.py"
    script.write_text(
        textwrap.dedent(_ENGINE_SOURCE)
        .replace("__DELAY_S__", repr(float(delay_s)))
        .replace("__SLOW_ONLY_AS_WHITE__", repr(bool(slow_only_as_white))),
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    if not sys.platform.startswith("win"):
        return script
    # Windows は shebang 付き .py を直接 CreateProcess できないため .bat ラッパを使う。
    launcher = script.with_suffix(".bat")
    launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    return launcher


def _write_engine_assets(
    root: Path,
    *,
    delay_s: float,
    white_engine_delay_s: float | None = None,
    slow_only_as_white: bool = False,
) -> tuple[Path, Path]:
    """2つの engine 定義を書き出す。

    既定では両者とも同じ挙動。``white_engine_delay_s`` / ``slow_only_as_white`` を渡すと
    2つ目の engine だけを別挙動にでき、1 run の中に時間切れする局としない局を作れる。
    """
    engines_dir = root / "engines"
    engines_dir.mkdir(parents=True, exist_ok=True)

    specs = [
        ("Composition Black", "engine_black", delay_s, False),
        (
            "Composition White",
            "engine_white",
            delay_s if white_engine_delay_s is None else white_engine_delay_s,
            slow_only_as_white,
        ),
    ]

    paths: list[Path] = []
    for display_name, script_name, engine_delay, engine_slow_only_as_white in specs:
        launcher = _write_engine_script(
            engines_dir,
            script_name,
            delay_s=engine_delay,
            slow_only_as_white=engine_slow_only_as_white,
        )
        engine_yaml = engines_dir / f"{script_name}.yaml"
        engine_yaml.write_text(
            yaml.safe_dump({"name": display_name, "engine_path": launcher.as_posix()}),
            encoding="utf-8",
        )
        paths.append(engine_yaml)
    return paths[0], paths[1]


async def _inject_loop_stalls(*, stall_s: float, interval_s: float) -> None:
    """event loop を同期的に塞いで停滞を作る（shadow 計測と同じ手法）。

    ``time.sleep`` は loop を明け渡さないため、watchdog から見て実際の lag になる。
    """
    while True:
        await asyncio.sleep(interval_s)
        time.sleep(stall_s)


@contextlib.asynccontextmanager
async def _loop_stalls(*, stall_s: float, interval_s: float) -> AsyncIterator[None]:
    task = asyncio.create_task(_inject_loop_stalls(stall_s=stall_s, interval_s=interval_s))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _block_after_first_bestmove_observation(
    monkeypatch: pytest.MonkeyPatch,
    *,
    block_s: float,
) -> None:
    """最初の bestmove 観測後だけ event loop を塞ぎ、処理側の時間切れを作る。"""
    original = GameRunnerLoopMixin._observation_of
    blocked = False

    def block_once(think_result: object) -> tuple[float | None, str | None]:
        nonlocal blocked
        observation = original(think_result)
        if not blocked:
            blocked = True
            time.sleep(block_s)
        return observation

    monkeypatch.setattr(GameRunnerLoopMixin, "_observation_of", staticmethod(block_once))


def _block_after_parallel_bestmove_observations(
    monkeypatch: pytest.MonkeyPatch,
    *,
    observation_count: int,
    block_s: float,
) -> None:
    """並列局の bestmove が揃ってから event loop を塞ぎ、全局を in-flight に保つ。"""
    original = GameRunnerFinalizeMixin._process_move_result
    arrived = 0
    observations_ready = asyncio.Event()

    async def wait_and_block(
        self: Any,
        board: Any,
        think_result: Any,
        engine_name: str,
    ) -> Any:
        nonlocal arrived
        arrived += 1
        if arrived == observation_count:
            time.sleep(block_s)
            observations_ready.set()
        elif arrived < observation_count:
            await asyncio.wait_for(observations_ready.wait(), timeout=5.0)
        return await original(self, board, think_result, engine_name)

    monkeypatch.setattr(GameRunnerFinalizeMixin, "_process_move_result", wait_and_block)


def _write_config(
    root: Path,
    *,
    black: Path,
    white: Path,
    time_ms: int,
    margin_ms: int,
    games_per_pair: int = 2,
    num_parallel: int = 1,
) -> Path:
    config_path = root / "tournament.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "experiment_name": "composition-timeout",
                "engines": [
                    {"name": "Composition Black", "engine_path": black.as_posix()},
                    {"name": "Composition White", "engine_path": white.as_posix()},
                ],
                "tournament": {
                    "scheduler": "round_robin",
                    "games_per_pair": games_per_pair,
                    "num_parallel": num_parallel,
                    "seed": 1,
                    "engine_lifecycle": "per_game",
                },
                "rules": {
                    "initial_positions": {"type": "startpos", "flip_policy": "pair_both"},
                    "adjudication": {
                        "enable_max_plies": True,
                        "max_plies": 4,
                        "sync_max_plies_with_engine": False,
                    },
                    "repetition_occurrences_to_draw": 4,
                    "time_control": {
                        "time_ms": time_ms,
                        "increment_ms": 0,
                        "expiry_margin_ms": margin_ms,
                    },
                },
                "dashboard": {"enabled": False},
                "system": {"path_preflight": "off", "resource_capacity_preflight": "off"},
            }
        ),
        encoding="utf-8",
    )
    return config_path


async def _run_tournament(
    root: Path,
    *,
    delay_s: float,
    time_ms: int,
    margin_ms: int,
    games_per_pair: int = 2,
    num_parallel: int = 1,
    stall_s: float = 0.0,
    stall_interval_s: float = 0.02,
    white_engine_delay_s: float | None = None,
    slow_only_as_white: bool = False,
) -> dict[str, object]:
    black, white = _write_engine_assets(
        root,
        delay_s=delay_s,
        white_engine_delay_s=white_engine_delay_s,
        slow_only_as_white=slow_only_as_white,
    )
    config_path = _write_config(
        root,
        black=black,
        white=white,
        time_ms=time_ms,
        margin_ms=margin_ms,
        games_per_pair=games_per_pair,
        num_parallel=num_parallel,
    )
    run_dir = root / "run"

    config = load_tournament_config(config_path)
    runner = build_tournament_runner(
        config,
        storage=create_run_storage(run_dir),
        should_skip_resume=True,
    )
    if stall_s > 0.0:
        async with _loop_stalls(stall_s=stall_s, interval_s=stall_interval_s):
            await runner.run()
    else:
        await runner.run()

    status_path = run_dir / "completion_status.json"
    assert status_path.exists(), "production composition must write the terminal run-health artifact"
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _timeout_origins_in_db(run_dir: Path) -> list[tuple[str, str]]:
    """``game.db`` に記録された ``(game_name, timeout_origin)`` を取り出す。

    artifact の集計だけでなく、局ごとに独立して判定されたことを見るために使う。
    """
    db_path = run_dir / "game.db"
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT g.game_name, a.origin FROM game_timeout_attribution AS a "
            "JOIN game AS g ON g.id = a.game_id ORDER BY g.game_name"
        ).fetchall()
    return [(str(name), str(origin)) for name, origin in rows]


def _white_player_by_game(run_dir: Path) -> dict[str, str]:
    """``game_name -> 白番 engine 名``。どの局が遅い側を白に持つかを特定する。"""
    db_path = run_dir / "game.db"
    if not db_path.exists():
        return {}
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT g.game_name, p.player_name FROM game AS g JOIN player AS p ON p.id = g.white_player_id"
        ).fetchall()
    return {str(name): str(player) for name, player in rows}


@pytest.mark.asyncio
async def test_production_composition_starts_the_watchdog_and_records_it(tmp_path: Path) -> None:
    """watchdog が production 配線で実際に動き、artifact へ計測値が載ること。

    `watchdog` が ``None`` なら run 中に watchdog が起動していない（0047 の事故そのもの）。
    """

    status = await _run_tournament(tmp_path, delay_s=0.0, time_ms=5000, margin_ms=500)

    watchdog = status.get("watchdog")
    assert isinstance(watchdog, dict), "watchdog must be measured through the production composition"
    assert watchdog["is_coverage_complete"] is True
    assert status["status"] in {"clean", "with-anomalies"}
    assert status["termination_reason"] == "schedule-complete"


@pytest.mark.asyncio
async def test_production_composition_classifies_a_genuine_engine_timeout(tmp_path: Path) -> None:
    """engine だけが遅い場合、production 配線でも ``engine_deadline`` として記録されること。"""

    status = await _run_tournament(tmp_path, delay_s=0.3, time_ms=1, margin_ms=0)

    origins = status.get("timeouts_by_origin")
    assert isinstance(origins, dict)
    assert origins.get("engine_deadline", 0) > 0, f"expected an engine_deadline timeout, got {origins}"
    # 正当な時間切れ負けなので無効局にはしない。
    assert status["error_games"] == 0
    assert status["status"] == "clean"


_INVALID_ORIGINS = {"orchestrator_stall", "unknown", "transport_timeout"}


@pytest.mark.asyncio
async def test_production_composition_does_not_blame_the_engine_when_the_loop_stalls(tmp_path: Path) -> None:
    """engine の遅延と event loop 停滞が重なったら engine のせいにしないこと。

    0047 の誤りは「遅延の原因を問わず時間切れ負けにする」ことだった。
    停滞が超過を説明できてしまう窓では因果を確定できないので、無効局にする。
    """

    # engine は budget 内で応答するが、停滞が観測を deadline の後ろへ押し出す。
    status = await _run_tournament(
        tmp_path,
        delay_s=0.02,
        time_ms=50,
        margin_ms=0,
        games_per_pair=2,
        stall_s=0.5,
        stall_interval_s=0.005,
    )

    watchdog = status.get("watchdog")
    assert isinstance(watchdog, dict)
    assert watchdog["loop_lag_events"] > 0, "the injected stall must be visible to the watchdog"

    origins = status.get("timeouts_by_origin")
    assert isinstance(origins, dict) and origins, f"a timeout must have been classified, got {origins}"
    assert origins.get("engine_deadline", 0) == 0, (
        f"a stall that can explain the overshoot must not be attributed to the engine: {origins}"
    )
    assert set(origins) <= _INVALID_ORIGINS, f"unexpected origin under a sustained stall: {origins}"
    # 無効局は勝敗を付けないので ERROR として残る。
    assert status["error_games"] > 0


@pytest.mark.asyncio
async def test_production_composition_records_a_positive_orchestrator_stall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """orchestrator 側の停滞を positive に立証できること。

    engine は即答するので観測時刻は deadline より前にある。それでも clock が尽きるのは
    観測後の停滞が原因なので、``orchestrator_stall`` として engine と切り分けられる。
    ``unknown``（因果不明）とは別の、証拠のある分類であることを production 配線で確認する。
    """

    _block_after_first_bestmove_observation(monkeypatch, block_s=0.75)

    status = await _run_tournament(
        tmp_path,
        delay_s=0.0,
        time_ms=500,
        margin_ms=0,
        games_per_pair=2,
    )

    origins = status.get("timeouts_by_origin")
    assert isinstance(origins, dict)
    assert origins.get("orchestrator_stall", 0) > 0, f"expected a positive orchestrator_stall, got {origins}"
    assert origins.get("engine_deadline", 0) == 0, f"the engine answered in time: {origins}"
    # 無効局なので rating / SPRT へは入らない。
    assert status["error_games"] >= origins["orchestrator_stall"]


@pytest.mark.asyncio
async def test_production_composition_reports_coverage_loss_as_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ring からあふれて coverage を失ったら `unknown` にすること。

    lag 0 と「証拠が無い」を混同すると、計測できていないだけの窓を engine のせいにする。
    ring 長は tunable なので、production 配線のまま最小値へ縮めて overflow を起こす。
    """

    import functools

    from shogiarena._core.contexts.game_session.application.session import base_session_runner
    from shogiarena._core.shared.kernel.runtime_watchdog import RuntimeWatchdog

    monkeypatch.setattr(
        base_session_runner,
        "RuntimeWatchdog",
        functools.partial(RuntimeWatchdog, ring_size=1),
    )

    status = await _run_tournament(
        tmp_path,
        delay_s=0.02,
        time_ms=50,
        margin_ms=0,
        games_per_pair=2,
        stall_s=0.5,
        stall_interval_s=0.005,
    )

    watchdog = status.get("watchdog")
    assert isinstance(watchdog, dict)
    assert watchdog["dropped_loop_events"] > 0, "the shrunken ring must actually overflow"
    assert watchdog["is_coverage_complete"] is False

    origins = status.get("timeouts_by_origin")
    assert isinstance(origins, dict) and origins
    assert origins.get("engine_deadline", 0) == 0, f"coverage loss must not resolve to the engine: {origins}"
    assert status["coverage_incomplete_timeouts"] > 0


@pytest.mark.asyncio
async def test_production_composition_keeps_a_separate_decision_per_game(tmp_path: Path) -> None:
    """並行する2局以上で timeout 判定が混ざらないこと。

    runner は worker 間で共有されるため、per-game holder を持たないと
    直前の局の判定が次の局へ漏れる。
    """

    # 片方の engine だけ「白番のときだけ遅い」ようにする。``pair_both`` で色が入れ替わるため、
    # 同じ run に「時間切れする局」と「しない局」が決定的に混在する。
    # 全局を同条件にすると、holder を共有して全局へ同じ origin を漏らす実装でも通ってしまう。
    slow_engine = "Composition White"
    # 速い側と遅い側の差は、負荷が乗っても取り違えない幅にする。
    # budget 400ms に対し、速い側は数十 ms、遅い側は 1s 眠る。
    status = await _run_tournament(
        tmp_path,
        delay_s=0.0,
        white_engine_delay_s=1.0,
        slow_only_as_white=True,
        time_ms=400,
        margin_ms=0,
        games_per_pair=4,
        num_parallel=2,
    )

    run_dir = tmp_path / "run"
    rows = _timeout_origins_in_db(run_dir)
    white_by_game = _white_player_by_game(run_dir)
    assert len(white_by_game) == 4, f"expected 4 completed games, got {white_by_game}"

    games_with_row = {name for name, _ in rows}
    games_with_slow_white = {name for name, white in white_by_game.items() if white == slow_engine}

    # 混在していること自体を先に確認する（全局同条件なら以降の検査に意味がない）。
    assert 0 < len(games_with_slow_white) < len(white_by_game), (
        f"the scenario must mix timing out and not timing out: {white_by_game}"
    )
    # 遅い engine が白番の局だけに attribution row が付く。holder を共有していると
    # 時間切れしていない局にも前局の origin が漏れて、この等式が崩れる。
    assert games_with_row == games_with_slow_white, (
        f"attribution must follow each game's own evidence: rows={sorted(games_with_row)} "
        f"expected={sorted(games_with_slow_white)} white_by_game={white_by_game}"
    )
    # 局ごとに1行だけ。
    game_names = [name for name, _ in rows]
    assert len(set(game_names)) == len(game_names), f"attribution rows must be per game: {rows}"

    origins = status.get("timeouts_by_origin")
    assert isinstance(origins, dict)
    assert sum(origins.values()) == len(rows), (
        f"the artifact and game.db must agree on the attribution count: {origins} vs {rows}"
    )


def test_only_the_tournament_orchestrator_opts_into_reclassification() -> None:
    """再分類の opt-in は tournament だけで、SPSA は既定の無効のままであること。

    再分類は無効局を作るため、SPSA の探索を勝手に止めないよう既定は無効に固定する。
    これは実 SPSA run ではなく orchestrator の配線で確認している（verification.md 参照）。
    """

    from shogiarena._core.contexts.game_session.adapters.orchestration.contracts_base_orchestrator import (
        BaseOrchestrator,
    )
    from shogiarena._core.contexts.spsa.adapters.orchestrator import SpsaOrchestrator
    from shogiarena._core.contexts.tournament.adapters.orchestrator import TournamentOrchestrator

    assert BaseOrchestrator._timeout_reclassification_enabled is False
    assert TournamentOrchestrator._timeout_reclassification_enabled is True
    assert SpsaOrchestrator._timeout_reclassification_enabled is False


@pytest.mark.asyncio
async def test_production_composition_writes_a_cancelled_terminal_status(tmp_path: Path) -> None:
    """cancellation でも terminal status が残ること（task 0052 / review 指摘）。

    cancellation は finalize へ到達しないため、そこで書かないと公開文書の
    ``status=failed`` / ``termination_reason=cancelled`` が実運用で成立しない。
    """

    black, white = _write_engine_assets(tmp_path, delay_s=0.5)
    config_path = _write_config(tmp_path, black=black, white=white, time_ms=60_000, margin_ms=500)
    run_dir = tmp_path / "run"

    config = load_tournament_config(config_path)
    runner = build_tournament_runner(config, storage=create_run_storage(run_dir), should_skip_resume=True)

    task = asyncio.create_task(runner.run())
    # engine が思考している間に停止させる。
    await asyncio.sleep(0.4)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    status_path = run_dir / "completion_status.json"
    assert status_path.exists(), "a cancelled run must still commit a terminal run-health artifact"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["termination_reason"] == "cancelled"
    # 完了 marker は作らない。
    assert not (run_dir / "completed.flag").exists()
    # cleanup 後の確定 status へ昇格していること（暫定のまま残っていない）。
    assert status["is_provisional"] is False
    # cleanup 前に確定させた watchdog 計測値を伴うこと。timeout 診断の主要な材料なので、
    # 中断した run こそ欠けてはいけない。
    assert isinstance(status.get("watchdog"), dict), f"a cancelled run must carry the watchdog summary: {status}"
