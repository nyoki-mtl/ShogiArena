import asyncio
import os
import textwrap
from pathlib import Path

import pytest
from rshogi.core import Move

from shogiarena._core.contexts.instances.application.instance_models import Instance, InstanceConfig, InstanceType
from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkRequest
from shogiarena._core.platform.engine_provisioning.spawner_backed_usi_bridge import SpawnerBackedUSIBridge
from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.platform.engine_runtime.usi_engine_session import AsyncUsiEngine, UsiEngineState


def _write_mock_usi_engine(script_path: Path) -> None:
    script_path.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import sys
            import time

            def send(line: str) -> None:
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
                    send("id name IntegrationMock")
                    send("id author TestHarness")
                    send("option name Threads type spin default 1 min 1 max 1")
                    send("usiok")
                elif line == "isready":
                    send("readyok")
                elif line == "usinewgame":
                    continue
                elif line.startswith("setoption "):
                    continue
                elif line.startswith("position "):
                    continue
                elif line.startswith("go mate"):
                    time.sleep(0.01)
                    send("checkmate 7g7f 3c3d")
                elif line.startswith("go infinite"):
                    send("info string analysis start")
                    time.sleep(0.01)
                    send("info depth 1 nodes 123 time 10 nps 999 pv 7g7f")
                    pending_stop_reply = "bestmove 7g7f ponder 3c3d"
                elif line.startswith("go "):
                    send("info depth 5 nodes 500 time 20 nps 2500 pv 7g7f")
                    time.sleep(0.05)
                    tokens = line.split()
                    if "ponder" in tokens:
                        send("bestmove 2g2f ponder 3c3d")
                    else:
                        send("bestmove 7g7f ponder 3c3d")
                elif line.startswith("ponderhit"):
                    send("info string ponderhit")
                elif line == "stop":
                    if pending_stop_reply:
                        send(pending_stop_reply)
                        pending_stop_reply = None
                    else:
                        send("bestmove 7g7f")
                elif line == "gameover resign" or line.startswith("gameover "):
                    continue
                elif line == "quit":
                    break

            """
        ),
        encoding="utf-8",
    )
    os.chmod(script_path, 0o755)


async def _wait_for_state(engine: AsyncUsiEngine, expected: UsiEngineState, timeout: float = 1.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if engine.state == expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Engine state did not reach {expected} within {timeout} seconds (current={engine.state})")


@pytest.mark.asyncio
async def test_async_usi_engine_state_machine_with_subprocess(tmp_path: Path) -> None:
    script_path = tmp_path / "mock_usi_engine.py"
    _write_mock_usi_engine(script_path)

    config = UsiEngineConfig.from_mapping(
        {
            "name": "IntegrationMock",
            "engine_path": str(script_path),
            "options": {"Threads": 1},
        }
    )
    instance = Instance(
        config=InstanceConfig(
            name="local-instance",
            type=InstanceType.LOCAL,
            engine_dir="",
        )
    )
    bridge = SpawnerBackedUSIBridge(
        instance=instance,
        engine_path=str(script_path),
        name="integration-mock",
    )

    async with AsyncUsiEngine(config=config, bridge=bridge) as engine:
        await _wait_for_state(engine, UsiEngineState.READY)

        await engine.new_game()
        await _wait_for_state(engine, UsiEngineState.READY)

        request = UsiThinkRequest(movetime=100)
        think_task = asyncio.create_task(engine.think(sfen="startpos", request=request))
        await asyncio.sleep(0.01)
        assert engine.state == UsiEngineState.WAITING_FOR_BESTMOVE
        result = await think_task
        assert result.bestmove == Move.from_usi("7g7f")
        await _wait_for_state(engine, UsiEngineState.READY)

        ponder_task = asyncio.create_task(
            engine.think(sfen="startpos", request=UsiThinkRequest(movetime=100, is_ponder=True))
        )
        await asyncio.sleep(0.01)
        assert engine.state == UsiEngineState.PONDER
        ponder_result = await ponder_task
        assert ponder_result.bestmove == Move.from_usi("2g2f")
        await _wait_for_state(engine, UsiEngineState.READY)

        stop_task = asyncio.create_task(engine.think(sfen="startpos", request=UsiThinkRequest(movetime=200)))
        await asyncio.sleep(0.01)
        assert engine.state == UsiEngineState.WAITING_FOR_BESTMOVE
        stop_result = await engine.stop(timeout=1.0)
        assert stop_result is not None
        assert stop_result.bestmove == Move.from_usi("7g7f")
        final_result = await stop_task
        assert final_result.bestmove == Move.from_usi("7g7f")
        await _wait_for_state(engine, UsiEngineState.READY)

        mate_result = await engine.think_mate(sfen="startpos")
        assert mate_result.is_mate
        await _wait_for_state(engine, UsiEngineState.READY)

        analysis_handle = await engine.analyze(sfen="startpos", request=UsiThinkRequest(is_infinite=True))
        await asyncio.sleep(0.01)
        assert engine.state == UsiEngineState.WAITING_FOR_BESTMOVE
        await analysis_handle.stop()
        await _wait_for_state(engine, UsiEngineState.READY)
