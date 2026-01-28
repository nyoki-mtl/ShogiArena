import asyncio
from collections.abc import Awaitable, Callable

import pytest

from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine
from shogiarena.arena.engines.usi_bridge import AsyncUSIProcessBridgeProtocol
from shogiarena.arena.engines.usi_config import UsiEngineConfig
from shogiarena.arena.engines.usi_engine import AnalysisHandle, AsyncUsiEngine, PonderHitTimings, UsiEngineState
from shogiarena.arena.engines.usi_think import UsiThinkRequest
from shogiarena.arena.execution.engine_participant import EngineParticipant
from shogiarena.utils.types.types import GameResult


class DummyBridge(AsyncUSIProcessBridgeProtocol):
    def __init__(self) -> None:
        self._running = False
        self.commands: list[str] = []
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._handlers: dict[str, Callable[[str], Awaitable[None]]] = {}
        self._install_default_handlers()

    def _install_default_handlers(self) -> None:
        self._handlers["usi"] = self._handle_usi
        self._handlers["isready"] = self._handle_isready
        self._handlers["stop"] = self._handle_stop

    def set_handler(self, prefix: str, handler: Callable[[str], Awaitable[None]]) -> None:
        self._handlers[prefix] = handler

    async def start_process(self) -> None:  # noqa: D401
        self._running = True

    async def stop_process(self) -> None:  # noqa: D401
        self._running = False
        await self._queue.put(None)

    async def send_line(self, command: str) -> None:  # noqa: D401
        self.commands.append(command)
        for prefix, handler in self._handlers.items():
            if command.startswith(prefix):
                await handler(command)
                break

    def receive_lines(self):  # noqa: D401
        async def iterator():
            while True:
                line = await self._queue.get()
                if line is None:
                    break
                yield line

        return iterator()

    def is_running(self) -> bool:  # noqa: D401
        return self._running

    async def _handle_usi(self, command: str) -> None:
        await self._queue.put("id name DummyEngine")
        await self._queue.put("option name Threads type spin default 1 min 1 max 1")
        await self._queue.put("usiok")

    async def _handle_isready(self, command: str) -> None:
        await self._queue.put("readyok")

    async def _handle_stop(self, command: str) -> None:
        await self._queue.put("bestmove 7g7f")

    async def enqueue(self, line: str) -> None:
        await self._queue.put(line)


@pytest.mark.asyncio
async def test_engine_handshake_applies_options(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
            "options": {"Threads": 1},
        }
    )
    bridge = DummyBridge()
    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        assert any(cmd.startswith("setoption name Threads") for cmd in bridge.commands)
        assert eng.engine_info.get("name") == "DummyEngine"


@pytest.mark.asyncio
async def test_engine_recognises_spsa_param_lines(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
            "options": {"Search_razoring_1": 495},
        }
    )
    bridge = DummyBridge()

    async def handle_usi(command: str) -> None:
        await bridge.enqueue("id name DummyEngine")
        await bridge.enqueue("Search_razoring_1,495,0,990,49.5,0.0020")
        await bridge.enqueue("usiok")

    bridge.set_handler("usi", handle_usi)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        assert "Search_razoring_1" in eng._options  # type: ignore[attr-defined]
        assert any(cmd.startswith("setoption name Search_razoring_1") for cmd in bridge.commands)
        opt = eng._options["Search_razoring_1"]  # type: ignore[attr-defined]
        assert opt.option_type == "spin"
        assert opt.minimum == 0
        assert opt.maximum == 990


@pytest.mark.asyncio
async def test_engine_think_returns_bestmove(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
            "enable_early_ponder": True,
        }
    )
    bridge = DummyBridge()

    async def handle_go(command: str) -> None:
        await bridge.enqueue("info depth 10 nodes 123 time 45")
        await bridge.enqueue("bestmove 7g7f ponder 3c3d")

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        bridge.set_handler("go ", handle_go)

        seen_infos: list[int] = []

        async def info_handler(pv):
            if pv.depth is not None:
                seen_infos.append(pv.depth)

        result = await eng.think(
            sfen="startpos",
            request=UsiThinkRequest(movetime=1000),
            info_handler=info_handler,
        )

        assert result.bestmove == "7g7f"
        assert result.ponder == "3c3d"
        assert seen_infos == [10]


@pytest.mark.asyncio
async def test_engine_think_timeout(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
            "enable_early_ponder": True,
        }
    )
    bridge = DummyBridge()

    async def noop_go(command: str) -> None:
        # No bestmove emitted to force timeout
        await asyncio.sleep(0)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        bridge.set_handler("go ", noop_go)

        with pytest.raises(asyncio.TimeoutError):
            await eng.think(
                sfen="startpos",
                request=UsiThinkRequest(movetime=10),
                timeout=0.05,
            )

        assert not any(cmd == "stop" for cmd in bridge.commands)


@pytest.mark.asyncio
async def test_engine_think_mate(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
            "enable_early_ponder": True,
        }
    )
    bridge = DummyBridge()

    async def handle_go_mate(command: str) -> None:
        await bridge.enqueue("checkmate 7g7f 3c3d")

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        bridge.set_handler("go mate", handle_go_mate)

        result = await eng.think_mate(sfen="startpos")
        assert result.is_mate
        assert result.moves == ("7g7f", "3c3d")


@pytest.mark.asyncio
async def test_engine_analyze_stop(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
            "enable_early_ponder": True,
        }
    )
    bridge = DummyBridge()

    async def handle_infinite(command: str) -> None:
        await bridge.enqueue("info depth 1")

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        bridge.set_handler("go infinite", handle_infinite)

        handle: AnalysisHandle = await eng.analyze(
            sfen="startpos",
            request=UsiThinkRequest(infinite=True),
        )

        await handle.stop()
        assert "stop" in bridge.commands


@pytest.mark.asyncio
async def test_engine_ponder_hit(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
            "enable_early_ponder": True,
        }
    )
    bridge = DummyBridge()

    async def handle_go(command: str) -> None:
        if " ponder" in command:
            return
        await bridge.enqueue("info depth 10")
        await bridge.enqueue("bestmove 7g7f ponder 3c3d")

    async def handle_go_ponder(command: str) -> None:
        await asyncio.sleep(0)

    async def handle_ponderhit(command: str) -> None:
        await bridge.enqueue("info depth 12")
        await bridge.enqueue("bestmove 2g2f ponder 8c8d")

    bridge.set_handler("go ponder", handle_go_ponder)
    bridge.set_handler("go ", handle_go)
    bridge.set_handler("ponderhit", handle_ponderhit)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        result = await eng.think(
            sfen="startpos",
            request=UsiThinkRequest(movetime=100),
        )

        assert result.bestmove == "7g7f"
        assert result.ponder == "3c3d"

        ponder_handle = await eng.start_ponder(
            sfen="startpos",
            moves=("7g7f", "3c3d"),
            request=UsiThinkRequest(movetime=100, ponder=True),
            predicted_move="3c3d",
        )
        assert eng.state == UsiEngineState.PONDER
        assert any(" ponder" in cmd and cmd.startswith("go ") for cmd in bridge.commands)

        ponder_result = await ponder_handle.hit(timings=PonderHitTimings(), timeout=1.0)

        assert ponder_result.bestmove == "2g2f"
        assert eng.state == UsiEngineState.READY


@pytest.mark.asyncio
async def test_engine_early_ponder_requires_timings(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
            "enable_early_ponder": True,
        }
    )
    bridge = DummyBridge()

    async def handle_go(command: str) -> None:
        if " ponder" in command:
            return
        await bridge.enqueue("bestmove 7g7f ponder 3c3d")

    async def handle_go_ponder(command: str) -> None:
        await asyncio.sleep(0)
        assert "btime" not in command
        assert "wtime" not in command
        assert "byoyomi" not in command

    async def handle_ponderhit(command: str) -> None:
        assert "btime" in command
        await bridge.enqueue("bestmove 2g2f")

    bridge.set_handler("go ", handle_go)
    bridge.set_handler("go ponder", handle_go_ponder)
    bridge.set_handler("ponderhit", handle_ponderhit)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        result = await eng.think(
            sfen="startpos",
            request=UsiThinkRequest(movetime=100),
        )

        assert result.ponder == "3c3d"

        ponder_handle = await eng.start_ponder(
            sfen="startpos",
            moves=("7g7f", "3c3d"),
            request=UsiThinkRequest(
                btime=10_000,
                wtime=12_000,
                byoyomi=200,
                ponder=True,
            ),
            predicted_move="3c3d",
        )
        go_commands = [cmd for cmd in bridge.commands if cmd.startswith("go ")]
        assert go_commands[-1] == "go ponder"

        with pytest.raises(ValueError):
            await ponder_handle.hit(timeout=1.0)

        timings = PonderHitTimings(btime=9500, wtime=12000, byoyomi=200)
        ponder_result = await ponder_handle.hit(timings=timings, timeout=1.0)

        assert ponder_result.bestmove == "2g2f"
        assert any(cmd.startswith("ponderhit btime 9500") for cmd in bridge.commands)


@pytest.mark.asyncio
async def test_engine_ponder_cancel(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async def handle_go(command: str) -> None:
        if " ponder" in command:
            return
        await bridge.enqueue("bestmove 7g7f ponder 3c3d")

    async def handle_go_ponder(command: str) -> None:
        await asyncio.sleep(0)

    async def handle_stop(command: str) -> None:
        await bridge.enqueue("bestmove 9i9h")

    bridge.set_handler("go ponder", handle_go_ponder)
    bridge.set_handler("go ", handle_go)
    bridge.set_handler("stop", handle_stop)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        result = await eng.think(
            sfen="startpos",
            request=UsiThinkRequest(movetime=100),
        )

        assert result.ponder == "3c3d"

        ponder_handle = await eng.start_ponder(
            sfen="startpos",
            moves=("7g7f", "3c3d"),
            request=UsiThinkRequest(movetime=100, ponder=True),
            predicted_move="3c3d",
        )
        cancel_result = await ponder_handle.cancel(timeout=1.0)

        assert cancel_result is not None
        assert cancel_result.bestmove == "9i9h"
        assert eng.state == UsiEngineState.READY


@pytest.mark.asyncio
async def test_engine_participant_wraps_engine(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async def handle_go(command: str) -> None:
        await bridge.enqueue("bestmove 7g7f")

    bridge.set_handler("go ", handle_go)

    engine = AsyncUsiEngine(config=config, bridge=bridge)
    participant = EngineParticipant(engine, role="black")

    await participant.prepare(initial_sfen="startpos")
    result = await participant.think(
        sfen="startpos",
        moves=(),
        request=UsiThinkRequest(movetime=100),
    )
    assert result.bestmove == "7g7f"

    await participant.notify_gameover(GameResult.BLACK_WIN)


def test_sync_engine_think(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async def handle_go(command: str) -> None:
        await bridge.enqueue("info depth 6 nodes 42 time 7")
        await bridge.enqueue("bestmove 7g7f ponder 3c3d")

    bridge.set_handler("go ", handle_go)

    async_engine = AsyncUsiEngine(config=config, bridge=bridge)
    with SyncUsiEngine.from_async_engine(async_engine) as engine:
        engine.new_game()
        result = engine.think(
            sfen="startpos",
            moves=(),
            request=UsiThinkRequest(movetime=1000),
        )
        assert result.bestmove == "7g7f"
        assert result.ponder == "3c3d"
