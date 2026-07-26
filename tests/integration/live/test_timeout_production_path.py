"""実 subprocess を通した timeout attribution（task 0052 / decisions.md Decision 15）。

0047 の unit / integration test はすべて fake watchdog と fake engine を検証していたため、
production 配線で watchdog が起動していなかった事故を検出できなかった。

ここでは次を **実物** で通す。

- Python の dummy USI engine を実 subprocess として起動する。
- ``SpawnerBackedUSIBridge`` → ``AsyncUsiEngine`` → ``EngineParticipant`` の production 経路。
- 実 ``RuntimeWatchdog``（daemon thread と loop heartbeat が実際に動く）。

これにより「``bestmove`` の受信時刻が bridge から attribution まで実際に届くか」と
「delivery coverage をローカル pipe として主張できるか」を配線ごと検証する。
"""

from __future__ import annotations

import asyncio
import os
import sys
import textwrap
from pathlib import Path

import pytest
from rsshogi.types import Color

from shogiarena._core.contexts.instances.application.instance_models import Instance, InstanceConfig, InstanceType
from shogiarena._core.contexts.match.application.engine_participant import EngineParticipant
from shogiarena._core.contexts.match.application.runner import GameRunner
from shogiarena._core.platform.engine_provisioning.spawner_backed_usi_bridge import SpawnerBackedUSIBridge
from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.platform.engine_runtime.usi_engine_session import AsyncUsiEngine
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.runtime_watchdog import RuntimeWatchdog
from shogiarena._core.shared.kernel.time_control import TimeControlLimits
from shogiarena._core.shared.kernel.timeout_attribution import ObservationBasis

_ENGINE_SOURCE = """\
#!/usr/bin/env python3
import os
import sys
import time

DELAY_S = float(os.environ.get("SHOGIARENA_TEST_ENGINE_DELAY_S", "0"))


def send(line):
    sys.stdout.write(line + "\\n")
    sys.stdout.flush()


pending_stop_reply = None

while True:
    raw_line = sys.stdin.readline()
    if not raw_line:
        break
    line = raw_line.strip()
    if not line:
        continue
    if line == "usi":
        send("id name TimeoutProbe")
        send("usiok")
    elif line == "isready":
        send("readyok")
    elif line == "quit":
        break
    elif line.startswith("go "):
        time.sleep(DELAY_S)
        send("bestmove 7g7f")
    elif line == "stop":
        if pending_stop_reply:
            send(pending_stop_reply)
            pending_stop_reply = None
        else:
            send("bestmove 7g7f")
"""


def _write_engine(tmp_path: Path, name: str) -> Path:
    script_path = tmp_path / f"{name}.py"
    script_path.write_text(textwrap.dedent(_ENGINE_SOURCE), encoding="utf-8")
    os.chmod(script_path, 0o755)
    if sys.platform.startswith("win"):
        # Windows は shebang 付き .py を直接 CreateProcess できないため .bat ラッパを使う。
        launcher = script_path.with_suffix(".bat")
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script_path}" %*\r\n', encoding="utf-8")
        return launcher
    return script_path


def _local_instance() -> Instance:
    return Instance(config=InstanceConfig(name="local-instance", type=InstanceType.LOCAL, engine_dir=""))


def _build_engine(launch_path: Path, name: str, *, delay_s: float) -> AsyncUsiEngine:
    config = UsiEngineConfig.from_mapping({"name": name, "engine_path": str(launch_path)})
    bridge = SpawnerBackedUSIBridge(
        instance=_local_instance(),
        engine_path=str(launch_path),
        name=name,
        env={**os.environ, "SHOGIARENA_TEST_ENGINE_DELAY_S": str(delay_s)},
    )
    return AsyncUsiEngine(config=config, bridge=bridge)


async def _run_game(
    tmp_path: Path,
    *,
    delay_s: float,
    limits: TimeControlLimits,
    game_id: str,
):  # type: ignore[no-untyped-def]
    launch_path = _write_engine(tmp_path, "timeout_probe")
    runner = GameRunner(
        progress_queue=None,
        time_control_limits=limits,
        adjudication_config=None,
        repetition_occurrences_to_draw=4,
    )
    watchdog = RuntimeWatchdog(interval_s=0.02)
    watchdog.start()
    runner.set_runtime_watchdog(watchdog)
    runner.set_timeout_reclassification(True)
    black_session = _build_engine(launch_path, "black", delay_s=delay_s)
    white_session = _build_engine(launch_path, "white", delay_s=delay_s)
    try:
        async with black_session, white_session:
            black = EngineParticipant(black_session, name_override="black", role=Color.BLACK)
            white = EngineParticipant(white_session, name_override="white", role=Color.WHITE)
            return await runner.run_game(
                black_engine=black,
                white_engine=white,
                initial_sfen="startpos",
                game_id=game_id,
                black_time_control_limits=limits,
                white_time_control_limits=limits,
            )
    finally:
        await watchdog.stop()


@pytest.mark.asyncio
async def test_local_bridge_reports_a_covered_observation_basis(tmp_path: Path) -> None:
    """ローカル subprocess の bridge が ``local_pipe`` として delivery coverage を主張できること。"""

    launch_path = _write_engine(tmp_path, "basis_probe")
    session = _build_engine(launch_path, "probe", delay_s=0.0)
    async with session:
        participant = EngineParticipant(session, name_override="probe")
        assert participant.bestmove_observation_basis() == ObservationBasis.LOCAL_PIPE.value


@pytest.mark.asyncio
async def test_bestmove_observation_timestamp_reaches_the_think_result(tmp_path: Path) -> None:
    """bridge が ``readline()`` 直後に採った時刻が think result まで届くこと。"""

    from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkRequest

    launch_path = _write_engine(tmp_path, "observation_probe")
    session = _build_engine(launch_path, "probe", delay_s=0.0)
    async with session:
        await session.new_game()
        result = await session.think(sfen="startpos", request=UsiThinkRequest(movetime=50))
        assert result.observed_at_s is not None
        assert result.observation_basis == ObservationBasis.LOCAL_PIPE.value


@pytest.mark.asyncio
async def test_a_genuinely_slow_engine_is_an_engine_deadline_on_the_production_path(tmp_path: Path) -> None:
    """loop 健全で engine だけが遅い場合は、実配線でも時間切れ負けのまま。"""

    # main time 1ms + margin 0: 300ms 使えば必ず超過する。wait timeout（1001ms）には届かないので、
    # ``update_after_move_expired`` 経路で観測済み bestmove を使った判定になる。
    limits = TimeControlLimits(time_ms=1, increment_ms=0, expiry_margin_ms=0)
    record = await _run_game(tmp_path, delay_s=0.3, limits=limits, game_id="production_engine_deadline")

    assert record.result in (GameResult.WHITE_WIN_BY_TIMEOUT, GameResult.BLACK_WIN_BY_TIMEOUT)
    assert record.metadata.attributes.get("timeout_origin") == "engine_deadline"


@pytest.mark.asyncio
async def test_a_bestmove_recovered_after_wait_timeout_keeps_its_engine_deadline_evidence(tmp_path: Path) -> None:
    """wait timeout 後の stop recovery でも local-pipe 観測を classifier まで保持すること。"""

    # wait timeout は main time + 1 秒。engine はその直後に bestmove を返すため、
    # TimeoutError 後の stop() が go 応答を回収する production recovery path を通る。
    limits = TimeControlLimits(time_ms=1, increment_ms=0, expiry_margin_ms=0)
    record = await _run_game(tmp_path, delay_s=1.2, limits=limits, game_id="production_recovered_timeout")

    assert record.result in (GameResult.WHITE_WIN_BY_TIMEOUT, GameResult.BLACK_WIN_BY_TIMEOUT)
    assert record.metadata.attributes.get("timeout_origin") == "engine_deadline"


@pytest.mark.asyncio
async def test_a_fast_engine_finishes_without_a_timeout_decision(tmp_path: Path) -> None:
    """正常な run では timeout origin が記録されないこと（回帰の下限）。"""

    limits = TimeControlLimits(time_ms=5000, increment_ms=0, expiry_margin_ms=500)
    record = await _run_game(tmp_path, delay_s=0.0, limits=limits, game_id="production_no_timeout")

    assert record.metadata.attributes.get("timeout_origin") is None


@pytest.mark.asyncio
async def test_concurrent_games_do_not_share_a_timeout_decision(tmp_path: Path) -> None:
    """worker 共有 runner でも、局ごとの decision holder が混線しないこと。"""

    slow_limits = TimeControlLimits(time_ms=1, increment_ms=0, expiry_margin_ms=0)
    fast_limits = TimeControlLimits(time_ms=5000, increment_ms=0, expiry_margin_ms=500)
    slow_dir = tmp_path / "slow"
    fast_dir = tmp_path / "fast"
    slow_dir.mkdir()
    fast_dir.mkdir()

    slow_record, fast_record = await asyncio.gather(
        _run_game(slow_dir, delay_s=0.3, limits=slow_limits, game_id="concurrent_slow"),
        _run_game(fast_dir, delay_s=0.0, limits=fast_limits, game_id="concurrent_fast"),
    )

    assert slow_record.metadata.attributes.get("timeout_origin") == "engine_deadline"
    assert fast_record.metadata.attributes.get("timeout_origin") is None
