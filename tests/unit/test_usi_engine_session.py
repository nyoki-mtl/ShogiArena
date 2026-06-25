import asyncio
import gc
import logging
import threading
from collections.abc import Awaitable, Callable

import pytest
from rshogi.core import Move
from rshogi.types import Color

from shogiarena._core.contexts.match.application.engine_participant import EngineParticipant
from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkRequest
from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.platform.engine_runtime.usi_engine_session import (
    AnalysisHandle,
    AsyncUsiEngine,
    PonderHitTimings,
    UsiEngineState,
)
from shogiarena._core.platform.engine_runtime.usi_engine_session_models import (
    EngineLifecycleEvent,
    EngineProcessInfo,
    UsiAnalyzePosition,
    UsiAnalyzeResetPolicy,
    UsiEngineStartError,
    UsiIoEvent,
)
from shogiarena._core.platform.engine_runtime.usi_protocol_types import AsyncUsiProcessBridgePort, UsiOption
from shogiarena._core.shared.kernel.game_results import GameResult


class DummyBridge(AsyncUsiProcessBridgePort):
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


class StopOrderBridge(DummyBridge):
    def __init__(self) -> None:
        super().__init__()
        self.engine: AsyncUsiEngine | None = None
        self.was_monitor_alive_during_stop = False

    async def stop_process(self) -> None:  # noqa: D401
        engine = self.engine
        self.was_monitor_alive_during_stop = (
            engine is not None and engine._monitor_task is not None and not engine._monitor_task.done()
        )
        self._running = False
        await self._queue.put(None)


class ProcessInfoBridge(DummyBridge):
    def get_process_info(self) -> EngineProcessInfo:
        return EngineProcessInfo(
            pid=12345,
            executable="/tmp/dummy-engine",
            working_directory="/tmp",
            command_line=("/tmp/dummy-engine", "--flag"),
        )


class StderrBridge(DummyBridge):
    def __init__(self) -> None:
        super().__init__()
        self._stderr_handler: Callable[[str], None] | None = None

    def set_stderr_handler(self, handler: Callable[[str], None] | None) -> None:
        self._stderr_handler = handler

    def emit_stderr(self, line: str) -> None:
        if self._stderr_handler is not None:
            self._stderr_handler(line)


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
async def test_get_usi_options_returns_option_declaration_snapshot(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        options = eng.get_usi_options()
        assert isinstance(options["Threads"], UsiOption)
        assert options["Threads"].option_type == "spin"
        assert options["Threads"].current == "1"

        options["Threads"].current = "99"
        assert eng.get_usi_options()["Threads"].current == "1"


@pytest.mark.asyncio
async def test_engine_exposes_process_info_and_lifecycle_events(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = ProcessInfoBridge()
    events: list[EngineLifecycleEvent] = []
    eng = AsyncUsiEngine(config=config, bridge=bridge)
    eng.register_lifecycle_handler(events.append)

    async with eng:
        assert eng.process_info is not None
        assert eng.process_info.pid == 12345

    names = [event.name for event in events]
    assert "process_started" in names
    assert "usiok" in names
    assert "options_applied" in names
    assert "readyok" in names
    assert "process_exited" in names
    assert events[-1].process_info is not None
    assert events[-1].process_info.pid == 12345


@pytest.mark.asyncio
async def test_engine_recognises_option_lines_for_custom_spsa_params(tmp_path) -> None:
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
        await bridge.enqueue("option name Search_razoring_1 type spin default 495 min 0 max 990")
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
async def test_engine_option_validation_can_allow_unlisted_combo_value(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async def handle_usi(command: str) -> None:
        await bridge.enqueue("id name DummyEngine")
        await bridge.enqueue("option name BookFile type combo default standard var standard var no_book")
        await bridge.enqueue("usiok")

    bridge.set_handler("usi", handle_usi)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        with pytest.raises(ValueError, match="must be one of"):
            await eng.apply_engine_options(
                {"BookFile": "user_book1.ybb"},
                clear_hash=False,
                after_setoption="none",
            )

        await eng.apply_engine_options(
            {"BookFile": "user_book1.ybb"},
            clear_hash=False,
            after_setoption="none",
            validation={"BookFile": "allow_unlisted_combo_value"},
        )

    assert "setoption name BookFile value user_book1.ybb" in bridge.commands


@pytest.mark.asyncio
async def test_engine_analyze_positions_reuses_session_with_reset_policy(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async def handle_usi(command: str) -> None:
        await bridge.enqueue("id name DummyEngine")
        await bridge.enqueue("option name Clear Hash type button")
        await bridge.enqueue("usiok")

    async def handle_go(command: str) -> None:
        await bridge.enqueue("info depth 1 score cp 10 pv 7g7f")
        await bridge.enqueue("bestmove 7g7f")

    bridge.set_handler("usi", handle_usi)
    bridge.set_handler("go ", handle_go)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        items = await eng.analyze_positions(
            (
                UsiAnalyzePosition(sfen="startpos"),
                UsiAnalyzePosition(sfen="startpos", moves=(Move.from_usi("7g7f"),)),
            ),
            request=UsiThinkRequest(depth=1),
            reset_policy=UsiAnalyzeResetPolicy(
                new_game=True,
                clear_hash_if_available=True,
                isready_before_each=True,
            ),
        )

    assert len(items) == 2
    assert all(item.result is not None for item in items)
    assert all(item.error is None for item in items)
    assert bridge.commands.count("usinewgame") == 2
    assert bridge.commands.count("setoption name Clear Hash") == 2


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

        assert result.bestmove == Move.from_usi("7g7f")
        assert result.ponder == Move.from_usi("3c3d")
        assert seen_infos == [10]


@pytest.mark.asyncio
async def test_engine_io_handler_receives_typed_event(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()
    events: list[UsiIoEvent] = []

    async def handle_go(command: str) -> None:
        await bridge.enqueue("info depth 10 nodes 123 time 45")
        await bridge.enqueue("bestmove 7g7f")

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        bridge.set_handler("go ", handle_go)
        eng.register_io_log_handler(events.append)
        await eng.think(sfen="startpos", request=UsiThinkRequest(movetime=1000))
        await eng.flush_io_log_handlers()

    info_event = next(event for event in events if event.line == "info depth 10 nodes 123 time 45")
    assert isinstance(info_event, UsiIoEvent)
    assert info_event.direction == "in"
    assert info_event.line == "info depth 10 nodes 123 time 45"
    assert info_event.phase is not None
    assert info_event.as_dict() == {
        "direction": "in",
        "line": "info depth 10 nodes 123 time 45",
        "monotonic_ns": info_event.monotonic_ns,
        "phase": info_event.phase,
        "timestamp_ms": info_event.timestamp_ms,
    }


@pytest.mark.asyncio
async def test_engine_stderr_io_handler_receives_stderr_event(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = StderrBridge()
    events: list[UsiIoEvent] = []

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        eng.register_io_log_handler(events.append)
        bridge.emit_stderr("warning line")
        await eng.flush_io_log_handlers()

    stderr_event = next(event for event in events if event.direction == "stderr")
    assert stderr_event.line == "warning line"
    assert stderr_event.phase is not None


@pytest.mark.asyncio
async def test_collect_raw_io_false_suppresses_in_out_events(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
            "io": {
                "collect_raw_io": False,
                "collect_outbound": True,
            },
        }
    )
    bridge = DummyBridge()
    events: list[UsiIoEvent] = []

    async def handle_go(command: str) -> None:
        await bridge.enqueue("info depth 10 nodes 123 time 45")
        await bridge.enqueue("bestmove 7g7f")

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        bridge.set_handler("go ", handle_go)
        eng.register_io_log_handler(events.append)
        result = await eng.think(sfen="startpos", request=UsiThinkRequest(movetime=1000))
        await eng.flush_io_log_handlers()

    assert result.bestmove == Move.from_usi("7g7f")
    assert not events


@pytest.mark.asyncio
async def test_engine_collects_info_strings_from_config_policy(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
            "io": {"collect_info_strings": True},
        }
    )
    bridge = DummyBridge()

    async def handle_go(command: str) -> None:
        await bridge.enqueue("info string trace busy=1")
        await bridge.enqueue("bestmove 7g7f")

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        bridge.set_handler("go ", handle_go)
        result = await eng.think(sfen="startpos", request=UsiThinkRequest(movetime=1000))

    assert result.info_strings == ("trace busy=1",)


@pytest.mark.asyncio
async def test_engine_think_progresses_while_sync_io_log_handler_is_blocked(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()
    handler_started = threading.Event()
    handler_release = threading.Event()

    async def handle_go(command: str) -> None:
        await bridge.enqueue("info depth 10 nodes 123 time 45")
        await bridge.enqueue("bestmove 7g7f ponder 3c3d")

    def blocking_handler(entry: UsiIoEvent) -> None:
        if entry.line == "info depth 10 nodes 123 time 45":
            handler_started.set()
            # Keep the handler blocked well past the assertion timeout so the test
            # proves think() is decoupled from synchronous log processing.
            handler_release.wait(timeout=1.0)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        bridge.set_handler("go ", handle_go)
        eng.register_io_log_handler(blocking_handler)

        think_task = asyncio.create_task(eng.think(sfen="startpos", request=UsiThinkRequest(movetime=1000)))
        started = await asyncio.to_thread(handler_started.wait, 1.0)
        assert started

        result = await asyncio.wait_for(think_task, timeout=0.05)
        handler_release.set()

        assert result.bestmove == Move.from_usi("7g7f")
        assert result.ponder == Move.from_usi("3c3d")


@pytest.mark.asyncio
async def test_engine_start_failure_phase_is_isready_on_ready_timeout(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
            "handshake_timeout": 0.05,
        }
    )
    bridge = DummyBridge()

    async def silent_isready(command: str) -> None:
        # Never emit readyok so the isready handshake times out.
        await asyncio.sleep(0)

    bridge.set_handler("isready", silent_isready)

    # Regression: the failure phase must be captured before close() rewinds the
    # state machine, otherwise an isready timeout is misreported as engine_start.
    with pytest.raises(UsiEngineStartError) as exc_info:
        async with AsyncUsiEngine(config=config, bridge=bridge):
            pass

    assert exc_info.value.failure_phase == "isready"


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
        await bridge.enqueue("info depth 9 nodes 321 time 12")
        await bridge.enqueue("checkmate 7g7f 3c3d")

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        bridge.set_handler("go mate", handle_go_mate)

        seen_depths: list[int] = []

        async def info_handler(pv) -> None:
            if pv.depth is not None:
                seen_depths.append(pv.depth)

        result = await eng.think_mate(sfen="startpos", info_handler=info_handler)
        assert result.is_mate
        assert result.moves == (Move.from_usi("7g7f"), Move.from_usi("3c3d"))
        assert seen_depths == [9]
        last_pv = result.get_last_pv()
        assert last_pv is not None
        assert last_pv.nodes == 321


@pytest.mark.asyncio
async def test_engine_think_mate_resolves_nomate_on_bestmove(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async def handle_go_mate(command: str) -> None:
        await bridge.enqueue("info depth 6 nodes 120 time 8")
        await bridge.enqueue("bestmove 0000")

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        bridge.set_handler("go mate", handle_go_mate)
        result = await eng.think_mate(sfen="startpos", timeout=1.0)
        assert not result.is_mate
        last_pv = result.get_last_pv()
        assert last_pv is not None
        assert last_pv.depth == 6


@pytest.mark.asyncio
async def test_engine_think_mate_supports_infinite_and_node_limit(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async def handle_go_mate(command: str) -> None:
        await bridge.enqueue("checkmate 7g7f")

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        bridge.set_handler("go mate", handle_go_mate)
        await eng.think_mate(sfen="startpos", is_infinite=True)
        await eng.think_mate(sfen="startpos", node_limit=456)

        go_mate_commands = [cmd for cmd in bridge.commands if cmd.startswith("go mate")]
        assert "go mate infinite" in go_mate_commands
        assert "go mate nodes 456" in go_mate_commands


@pytest.mark.asyncio
async def test_engine_think_mate_uses_config_default_limit(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
            "mate_default_ply_limit": 30000,
        }
    )
    bridge = DummyBridge()

    async def handle_go_mate(command: str) -> None:
        await bridge.enqueue("checkmate 7g7f")

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        bridge.set_handler("go mate", handle_go_mate)
        await eng.think_mate(sfen="startpos")
        assert "go mate 30000" in bridge.commands


@pytest.mark.asyncio
async def test_engine_think_mate_waits_for_bestmove_when_enabled(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async def handle_go_mate(command: str) -> None:
        await bridge.enqueue("checkmate 7g7f 3c3d")

        async def delayed_bestmove() -> None:
            await asyncio.sleep(0.03)
            await bridge.enqueue("bestmove 0000")

        asyncio.create_task(delayed_bestmove())

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        bridge.set_handler("go mate", handle_go_mate)
        result = await eng.think_mate(sfen="startpos", should_wait_for_bestmove=True, timeout=1.0)
        assert result.is_mate
        assert result.moves == (Move.from_usi("7g7f"), Move.from_usi("3c3d"))
        assert eng.state == UsiEngineState.READY


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
            request=UsiThinkRequest(is_infinite=True),
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

        assert result.bestmove == Move.from_usi("7g7f")
        assert result.ponder == Move.from_usi("3c3d")

        ponder_handle = await eng.start_ponder(
            sfen="startpos",
            moves=(Move.from_usi("7g7f"), Move.from_usi("3c3d")),
            request=UsiThinkRequest(movetime=100, is_ponder=True),
            predicted_move=Move.from_usi("3c3d"),
        )
        assert eng.state == UsiEngineState.PONDER
        assert any(" ponder" in cmd and cmd.startswith("go ") for cmd in bridge.commands)

        ponder_result = await ponder_handle.hit(timings=PonderHitTimings(), timeout=1.0)

        assert ponder_result.bestmove == Move.from_usi("2g2f")
        assert eng.state == UsiEngineState.READY


@pytest.mark.asyncio
async def test_engine_drops_early_ponder_bestmove_before_ponderhit(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async def handle_go_ponder(command: str) -> None:
        await asyncio.sleep(0)

    bridge.set_handler("go ponder", handle_go_ponder)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        ponder_handle = await eng.start_ponder(
            sfen="startpos",
            moves=(Move.from_usi("7g7f"),),
            request=UsiThinkRequest(movetime=100, is_ponder=True),
            predicted_move=Move.from_usi("3c3d"),
        )
        await bridge.enqueue("bestmove 2g2f")
        for _ in range(50):
            if eng.state == UsiEngineState.READY:
                break
            await asyncio.sleep(0.001)

        assert eng.state == UsiEngineState.READY
        assert not ponder_handle.is_active
        assert eng._bestmove_future is None  # noqa: SLF001
        with pytest.raises(RuntimeError, match="Ponder handle is no longer active"):
            await ponder_handle.hit(timeout=0.01)


@pytest.mark.asyncio
async def test_engine_ponderhit_accepts_bestmove_arriving_during_send(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async def handle_go_ponder(command: str) -> None:
        await asyncio.sleep(0)

    async def handle_ponderhit(command: str) -> None:
        await bridge.enqueue("bestmove 2g2f")
        await asyncio.sleep(0.01)

    bridge.set_handler("go ponder", handle_go_ponder)
    bridge.set_handler("ponderhit", handle_ponderhit)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        ponder_handle = await eng.start_ponder(
            sfen="startpos",
            moves=(Move.from_usi("7g7f"),),
            request=UsiThinkRequest(movetime=100, is_ponder=True),
            predicted_move=Move.from_usi("3c3d"),
        )

        ponder_result = await ponder_handle.hit(timeout=1.0)

        assert ponder_result.bestmove == Move.from_usi("2g2f")
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

        assert result.ponder == Move.from_usi("3c3d")

        ponder_handle = await eng.start_ponder(
            sfen="startpos",
            moves=(Move.from_usi("7g7f"), Move.from_usi("3c3d")),
            request=UsiThinkRequest(
                btime=10_000,
                wtime=12_000,
                byoyomi=200,
                is_ponder=True,
            ),
            predicted_move=Move.from_usi("3c3d"),
        )
        go_commands = [cmd for cmd in bridge.commands if cmd.startswith("go ")]
        assert go_commands[-1] == "go ponder"

        with pytest.raises(ValueError):
            await ponder_handle.hit(timeout=1.0)

        timings = PonderHitTimings(btime=9500, wtime=12000, byoyomi=200)
        ponder_result = await ponder_handle.hit(timings=timings, timeout=1.0)

        assert ponder_result.bestmove == Move.from_usi("2g2f")
        assert any(cmd.startswith("ponderhit btime 9500") for cmd in bridge.commands)


@pytest.mark.asyncio
async def test_engine_non_early_ponderhit_ignores_timing_payload(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
            "enable_early_ponder": False,
        }
    )
    bridge = DummyBridge()

    async def handle_go(command: str) -> None:
        if " ponder" in command:
            return
        await bridge.enqueue("bestmove 7g7f ponder 3c3d")

    async def handle_go_ponder(command: str) -> None:
        await asyncio.sleep(0)

    async def handle_ponderhit(command: str) -> None:
        assert command == "ponderhit"
        await bridge.enqueue("bestmove 2g2f")

    bridge.set_handler("go ", handle_go)
    bridge.set_handler("go ponder", handle_go_ponder)
    bridge.set_handler("ponderhit", handle_ponderhit)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        first = await eng.think(sfen="startpos", request=UsiThinkRequest(movetime=100))
        assert first.ponder == Move.from_usi("3c3d")

        ponder_handle = await eng.start_ponder(
            sfen="startpos",
            moves=(Move.from_usi("7g7f"), Move.from_usi("3c3d")),
            request=UsiThinkRequest(btime=10000, wtime=12000, binc=2000, winc=2000, is_ponder=True),
            predicted_move=Move.from_usi("3c3d"),
        )
        result = await ponder_handle.hit(
            timings=PonderHitTimings(btime=9000, wtime=10000, binc=2000, winc=2000),
            timeout=1.0,
        )
        assert result.bestmove == Move.from_usi("2g2f")


@pytest.mark.asyncio
async def test_engine_early_ponder_with_movetime_keeps_movetime_request(tmp_path) -> None:
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
        assert command == "go ponder movetime 100"

    async def handle_ponderhit(command: str) -> None:
        assert command == "ponderhit"
        await bridge.enqueue("bestmove 2g2f")

    bridge.set_handler("go ponder", handle_go_ponder)
    bridge.set_handler("go ", handle_go)
    bridge.set_handler("ponderhit", handle_ponderhit)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        first = await eng.think(sfen="startpos", request=UsiThinkRequest(movetime=100))
        assert first.ponder == Move.from_usi("3c3d")

        ponder_handle = await eng.start_ponder(
            sfen="startpos",
            moves=(Move.from_usi("7g7f"), Move.from_usi("3c3d")),
            request=UsiThinkRequest(movetime=100, is_ponder=True),
            predicted_move=Move.from_usi("3c3d"),
        )
        result = await ponder_handle.hit(timeout=1.0)
        assert result.bestmove == Move.from_usi("2g2f")


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

        assert result.ponder == Move.from_usi("3c3d")

        ponder_handle = await eng.start_ponder(
            sfen="startpos",
            moves=(Move.from_usi("7g7f"), Move.from_usi("3c3d")),
            request=UsiThinkRequest(movetime=100, is_ponder=True),
            predicted_move=Move.from_usi("3c3d"),
        )
        cancel_result = await ponder_handle.cancel(timeout=1.0)

        assert cancel_result is not None
        assert cancel_result.bestmove == Move.from_usi("9i9h")
        assert eng.state == UsiEngineState.READY


@pytest.mark.asyncio
async def test_engine_close_skips_stop_when_process_already_stopped_during_ponder(tmp_path, caplog) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
            "enable_early_ponder": True,
        }
    )
    bridge = DummyBridge()

    async def handle_go_ponder(command: str) -> None:
        return None

    bridge.set_handler("go ponder", handle_go_ponder)

    eng = AsyncUsiEngine(config=config, bridge=bridge)
    await eng.start()
    await eng.start_ponder(
        sfen="startpos",
        moves=[],
        request=UsiThinkRequest(movetime=100, is_ponder=True),
        predicted_move=Move.from_usi("3c3d"),
    )

    bridge._running = False
    caplog.set_level(logging.ERROR)

    await eng.close()
    await eng.close()

    assert not any("Error stopping ponder" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_engine_close_keeps_monitor_alive_until_process_stop(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = StopOrderBridge()
    eng = AsyncUsiEngine(config=config, bridge=bridge)
    bridge.engine = eng

    await eng.start()
    await eng.close()

    assert bridge.was_monitor_alive_during_stop


@pytest.mark.asyncio
async def test_engine_ignores_stale_bestmove_during_active_search(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async def handle_go(command: str) -> None:
        await asyncio.sleep(0)

    bridge.set_handler("go ", handle_go)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        think_task = asyncio.create_task(
            eng.think(
                sfen="startpos",
                request=UsiThinkRequest(movetime=1000),
                timeout=1.0,
            )
        )

        for _ in range(100):
            if eng.state == UsiEngineState.WAITING_FOR_BESTMOVE:
                break
            await asyncio.sleep(0.001)
        assert eng.state == UsiEngineState.WAITING_FOR_BESTMOVE

        eng._ignored_bestmove_count = 1  # noqa: SLF001
        await bridge.enqueue("bestmove 2g2f")
        await bridge.enqueue("bestmove 7g7f")

        result = await think_task
        assert result.bestmove == Move.from_usi("7g7f")
        assert eng.state == UsiEngineState.READY


@pytest.mark.asyncio
async def test_engine_cancel_ponder_timeout_recovers_and_allows_next_think(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()
    stale_bestmove_emitted = asyncio.Event()
    non_ponder_go_count = 0

    async def handle_go(command: str) -> None:
        nonlocal non_ponder_go_count
        if " ponder" in command:
            await asyncio.sleep(0)
            return
        non_ponder_go_count += 1
        if non_ponder_go_count == 1:
            await bridge.enqueue("bestmove 7g7f ponder 3c3d")
            return
        await bridge.enqueue("bestmove 6g6f")

    async def delayed_stop(command: str) -> None:
        async def emit_stale_bestmove() -> None:
            await asyncio.sleep(0.02)
            await bridge.enqueue("bestmove 2g2f")
            stale_bestmove_emitted.set()

        asyncio.create_task(emit_stale_bestmove())

    bridge.set_handler("go ", handle_go)
    bridge.set_handler("stop", delayed_stop)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        first = await eng.think(
            sfen="startpos",
            request=UsiThinkRequest(movetime=100),
        )
        assert first.ponder == Move.from_usi("3c3d")

        ponder_handle = await eng.start_ponder(
            sfen="startpos",
            moves=(Move.from_usi("7g7f"), Move.from_usi("3c3d")),
            request=UsiThinkRequest(movetime=100, is_ponder=True),
            predicted_move=Move.from_usi("3c3d"),
        )

        async def isready_after_stale_bestmove(command: str) -> None:
            await stale_bestmove_emitted.wait()
            await bridge.enqueue("readyok")

        bridge.set_handler("isready", isready_after_stale_bestmove)

        cancel_result = await ponder_handle.cancel(timeout=0.01)
        assert cancel_result is None
        assert eng.state == UsiEngineState.READY

        next_result = await eng.think(
            sfen="startpos",
            moves=(Move.from_usi("7g7f"),),
            request=UsiThinkRequest(movetime=100),
            timeout=1.0,
        )

        assert stale_bestmove_emitted.is_set()
        assert next_result.bestmove == Move.from_usi("6g6f")
        assert eng.state == UsiEngineState.READY


@pytest.mark.asyncio
async def test_engine_stop_does_not_send_duplicate_stop(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async def handle_go(command: str) -> None:
        await asyncio.sleep(0)

    async def handle_stop(command: str) -> None:
        await asyncio.sleep(0.05)
        await bridge.enqueue("bestmove 7g7f")

    bridge.set_handler("go ", handle_go)
    bridge.set_handler("stop", handle_stop)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        think_task = asyncio.create_task(eng.think(sfen="startpos", request=UsiThinkRequest(movetime=1000)))
        await asyncio.sleep(0.01)

        first_stop = asyncio.create_task(eng.stop(timeout=1.0))
        await asyncio.sleep(0.005)
        second_stop = asyncio.create_task(eng.stop(timeout=1.0))

        first_result = await first_stop
        second_result = await second_stop
        final_result = await think_task

        assert first_result is not None
        assert first_result.bestmove == Move.from_usi("7g7f")
        assert second_result is None or second_result.bestmove == Move.from_usi("7g7f")
        assert final_result.bestmove == Move.from_usi("7g7f")
        assert sum(1 for cmd in bridge.commands if cmd == "stop") == 1


@pytest.mark.asyncio
async def test_engine_think_timeout_can_recover_with_stop(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async def handle_go(command: str) -> None:
        async def delayed_bestmove() -> None:
            await asyncio.sleep(0.05)
            await bridge.enqueue("bestmove 7g7f")

        asyncio.create_task(delayed_bestmove())

    async def handle_stop(command: str) -> None:
        await asyncio.sleep(0)

    bridge.set_handler("go ", handle_go)
    bridge.set_handler("stop", handle_stop)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        with pytest.raises(asyncio.TimeoutError):
            await eng.think(
                sfen="startpos",
                request=UsiThinkRequest(movetime=1000),
                timeout=0.01,
            )

        recovered = await eng.stop(timeout=1.0)
        assert recovered is not None
        assert recovered.bestmove == Move.from_usi("7g7f")
        assert eng.state == UsiEngineState.READY


@pytest.mark.asyncio
async def test_engine_stop_recovers_while_sync_io_log_handler_is_blocked(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()
    handler_started = threading.Event()
    handler_release = threading.Event()

    async def handle_go(command: str) -> None:
        await bridge.enqueue("info string trace busy=1")

    async def handle_stop(command: str) -> None:
        await bridge.enqueue("bestmove 7g7f")

    def blocking_handler(entry: UsiIoEvent) -> None:
        if entry.line == "info string trace busy=1":
            handler_started.set()
            # Keep the handler blocked well past the assertion timeout so the test
            # proves stop() recovery is decoupled from synchronous log processing.
            handler_release.wait(timeout=1.0)

    bridge.set_handler("go ", handle_go)
    bridge.set_handler("stop", handle_stop)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        eng.register_io_log_handler(blocking_handler)

        think_task = asyncio.create_task(eng.think(sfen="startpos", request=UsiThinkRequest(movetime=1000)))
        started = await asyncio.to_thread(handler_started.wait, 1.0)
        assert started

        recovered = await asyncio.wait_for(eng.stop(timeout=1.0), timeout=0.05)
        handler_release.set()
        final_result = await think_task

        assert recovered is not None
        assert recovered.bestmove == Move.from_usi("7g7f")
        assert final_result.bestmove == Move.from_usi("7g7f")


@pytest.mark.asyncio
async def test_engine_ponderhit_timeout_can_recover_with_cancel(tmp_path) -> None:
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

    async def handle_ponderhit(command: str) -> None:
        async def delayed_bestmove() -> None:
            await asyncio.sleep(0.05)
            await bridge.enqueue("bestmove 2g2f")

        asyncio.create_task(delayed_bestmove())

    async def handle_stop(command: str) -> None:
        await asyncio.sleep(0)

    bridge.set_handler("go ", handle_go)
    bridge.set_handler("go ponder", handle_go)
    bridge.set_handler("ponderhit", handle_ponderhit)
    bridge.set_handler("stop", handle_stop)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        first = await eng.think(
            sfen="startpos",
            request=UsiThinkRequest(movetime=100),
        )
        assert first.ponder == Move.from_usi("3c3d")

        ponder_handle = await eng.start_ponder(
            sfen="startpos",
            moves=(Move.from_usi("7g7f"), Move.from_usi("3c3d")),
            request=UsiThinkRequest(movetime=100, is_ponder=True),
            predicted_move=Move.from_usi("3c3d"),
        )

        with pytest.raises(asyncio.TimeoutError):
            await ponder_handle.hit(timeout=0.01)

        recovered = await ponder_handle.cancel(timeout=1.0)
        assert recovered is not None
        assert recovered.bestmove == Move.from_usi("2g2f")
        assert eng.state == UsiEngineState.READY


@pytest.mark.asyncio
async def test_participant_start_ponder_without_usi_ponder_option(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async def handle_go(command: str) -> None:
        if " ponder" in command:
            await asyncio.sleep(0)
            return
        await bridge.enqueue("bestmove 7g7f ponder 3c3d")

    bridge.set_handler("go ", handle_go)
    bridge.set_handler("go ponder", handle_go)

    engine = AsyncUsiEngine(config=config, bridge=bridge)
    participant = EngineParticipant(engine, role=Color.BLACK)

    await participant.prepare(initial_sfen="startpos")
    result = await participant.think(
        sfen="startpos",
        moves=(),
        request=UsiThinkRequest(movetime=100),
    )
    assert result.ponder == Move.from_usi("3c3d")

    await participant.start_ponder(
        sfen="startpos",
        moves=(Move.from_usi("7g7f"), Move.from_usi("3c3d")),
        request=UsiThinkRequest(movetime=100, is_ponder=True),
        predicted_move=Move.from_usi("3c3d"),
    )

    assert any(cmd.startswith("go ") and " ponder" in cmd for cmd in bridge.commands)
    await participant.shutdown()


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
    participant = EngineParticipant(engine, role=Color.BLACK)

    await participant.prepare(initial_sfen="startpos")
    result = await participant.think(
        sfen="startpos",
        moves=(),
        request=UsiThinkRequest(movetime=100),
    )
    assert result.bestmove == Move.from_usi("7g7f")

    before_notify = len(bridge.commands)
    await participant.notify_gameover(GameResult.BLACK_WIN)
    notify_commands = bridge.commands[before_notify:]
    assert "gameover win" in notify_commands
    assert "isready" not in notify_commands


@pytest.mark.asyncio
async def test_engine_ignores_stale_bestmove_while_waiting_for_readyok(tmp_path, caplog) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:

        async def noisy_isready(command: str) -> None:
            await bridge.enqueue("bestmove 9i9h")
            await bridge.enqueue("readyok")

        bridge.set_handler("isready", noisy_isready)
        caplog.set_level(logging.WARNING)
        await eng.trigger_isready(timeout=1.0)

        assert eng.state == UsiEngineState.READY
        warning_messages = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
        assert not any("bestmove" in msg and "waiting_for_readyok" in msg for msg in warning_messages)


@pytest.mark.asyncio
async def test_trigger_isready_timeout_none_waits_without_deadline(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        eng._handshake_timeout = 0.01  # noqa: SLF001

        async def delayed_readyok(command: str) -> None:
            await asyncio.sleep(0.05)
            await bridge.enqueue("readyok")

        bridge.set_handler("isready", delayed_readyok)
        await eng.trigger_isready(timeout=None)
        assert eng.state == UsiEngineState.READY


@pytest.mark.asyncio
async def test_trigger_isready_wait_strategy_defers_until_search_finishes(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
            "isready_sync_strategy": "wait",
        }
    )
    bridge = DummyBridge()
    search_completed = asyncio.Event()
    release_bestmove = asyncio.Event()

    async def handle_go(command: str) -> None:
        async def delayed_bestmove() -> None:
            await release_bestmove.wait()
            await bridge.enqueue("bestmove 7g7f")
            search_completed.set()

        asyncio.create_task(delayed_bestmove())

    async def handle_isready(command: str) -> None:
        assert search_completed.is_set()
        await bridge.enqueue("readyok")

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        bridge.set_handler("go ", handle_go)
        bridge.set_handler("isready", handle_isready)

        think_task = asyncio.create_task(eng.think(sfen="startpos", request=UsiThinkRequest(movetime=1000)))
        for _ in range(50):
            if eng.state == UsiEngineState.WAITING_FOR_BESTMOVE:
                break
            await asyncio.sleep(0.001)
        assert eng.state == UsiEngineState.WAITING_FOR_BESTMOVE
        trigger_task = asyncio.create_task(eng.trigger_isready(timeout=1.0))
        await asyncio.sleep(0.01)
        assert not trigger_task.done()
        release_bestmove.set()
        await trigger_task
        result = await think_task
        assert result.bestmove == Move.from_usi("7g7f")
        assert eng.state == UsiEngineState.READY


@pytest.mark.asyncio
async def test_engine_gameover_waits_for_pending_ponder_bestmove_before_gameover(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()
    bestmove_released = asyncio.Event()
    gameover_seen = asyncio.Event()

    async def handle_go(command: str) -> None:
        if " ponder" in command:
            return
        await bridge.enqueue("bestmove 7g7f ponder 3c3d")

    async def handle_go_ponder(command: str) -> None:
        await asyncio.sleep(0)

    async def delayed_stop_reply(command: str) -> None:
        async def _emit_final_bestmove() -> None:
            await asyncio.sleep(0.01)
            await bridge.enqueue("bestmove 2g2f")
            bestmove_released.set()

        asyncio.create_task(_emit_final_bestmove())

    async def handle_gameover(command: str) -> None:
        assert bestmove_released.is_set()
        gameover_seen.set()

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        bridge.set_handler("go ", handle_go)
        bridge.set_handler("go ponder", handle_go_ponder)
        bridge.set_handler("stop", delayed_stop_reply)
        bridge.set_handler("gameover", handle_gameover)

        first = await eng.think(sfen="startpos", request=UsiThinkRequest(movetime=100))
        assert first.ponder == Move.from_usi("3c3d")

        await eng.start_ponder(
            sfen="startpos",
            moves=(Move.from_usi("7g7f"), Move.from_usi("3c3d")),
            request=UsiThinkRequest(movetime=100, is_ponder=True),
            predicted_move=Move.from_usi("3c3d"),
        )
        assert eng.state == UsiEngineState.PONDER

        await eng.gameover("draw")

        assert gameover_seen.is_set()
        assert eng._ignored_bestmove_count == 0  # noqa: SLF001


@pytest.mark.asyncio
async def test_engine_gameover_draw_stops_pending_ponder_and_allows_next_isready(tmp_path, caplog) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
        }
    )
    bridge = DummyBridge()
    release_stale_bestmove = asyncio.Event()

    async def handle_go(command: str) -> None:
        if " ponder" in command:
            return
        await bridge.enqueue("bestmove 7g7f ponder 3c3d")

    async def handle_go_ponder(command: str) -> None:
        await asyncio.sleep(0)

    async def delayed_stop_reply(command: str) -> None:
        async def _emit_late_bestmove() -> None:
            await release_stale_bestmove.wait()
            await bridge.enqueue("bestmove 2g2f")

        asyncio.create_task(_emit_late_bestmove())

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        eng._handshake_timeout = 0.01  # noqa: SLF001
        bridge.set_handler("go ", handle_go)
        bridge.set_handler("go ponder", handle_go_ponder)
        bridge.set_handler("stop", delayed_stop_reply)

        first = await eng.think(sfen="startpos", request=UsiThinkRequest(movetime=100))
        assert first.ponder == Move.from_usi("3c3d")

        await eng.start_ponder(
            sfen="startpos",
            moves=(Move.from_usi("7g7f"), Move.from_usi("3c3d")),
            request=UsiThinkRequest(movetime=100, is_ponder=True),
            predicted_move=Move.from_usi("3c3d"),
        )
        assert eng.state == UsiEngineState.PONDER

        await eng.gameover("draw")
        stop_indices = [idx for idx, cmd in enumerate(bridge.commands) if cmd == "stop"]
        gameover_indices = [idx for idx, cmd in enumerate(bridge.commands) if cmd == "gameover draw"]
        assert stop_indices
        assert gameover_indices
        assert stop_indices[-1] < gameover_indices[-1]

        async def isready_with_late_bestmove(command: str) -> None:
            release_stale_bestmove.set()
            await asyncio.sleep(0)
            await bridge.enqueue("readyok")

        bridge.set_handler("isready", isready_with_late_bestmove)
        caplog.set_level(logging.WARNING)
        await eng.trigger_isready(timeout=1.0)
        assert eng.state == UsiEngineState.READY
        await eng.new_game()
        next_result = await eng.think(sfen="startpos", request=UsiThinkRequest(movetime=100))
        assert next_result.bestmove == Move.from_usi("7g7f")

        warning_messages = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
        assert not any("bestmove" in msg and "waiting_for_readyok" in msg for msg in warning_messages)


@pytest.mark.asyncio
async def test_engine_gameover_without_late_bestmove_allows_next_game_think(tmp_path) -> None:
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

    async def stop_without_reply(command: str) -> None:
        await asyncio.sleep(0)

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        eng._handshake_timeout = 0.01  # noqa: SLF001
        bridge.set_handler("go ", handle_go)
        bridge.set_handler("go ponder", handle_go_ponder)
        bridge.set_handler("stop", stop_without_reply)

        first = await eng.think(sfen="startpos", request=UsiThinkRequest(movetime=100))
        assert first.ponder == Move.from_usi("3c3d")

        await eng.start_ponder(
            sfen="startpos",
            moves=(Move.from_usi("7g7f"), Move.from_usi("3c3d")),
            request=UsiThinkRequest(movetime=100, is_ponder=True),
            predicted_move=Move.from_usi("3c3d"),
        )
        assert eng.state == UsiEngineState.PONDER

        await eng.gameover("draw")
        await eng.trigger_isready(timeout=1.0)
        await eng.new_game()
        second = await eng.think(sfen="startpos", request=UsiThinkRequest(movetime=100))
        assert second.bestmove == Move.from_usi("7g7f")


@pytest.mark.asyncio
async def test_fail_pending_does_not_emit_unretrieved_future_exception(tmp_path) -> None:
    config = UsiEngineConfig.from_mapping(
        {
            "name": "Dummy",
            "engine_path": str(tmp_path / "engine"),
            "enable_early_ponder": True,
        }
    )
    bridge = DummyBridge()
    contexts: list[dict[str, object]] = []

    async with AsyncUsiEngine(config=config, bridge=bridge) as eng:
        await eng.start_ponder(
            sfen="startpos",
            moves=(),
            request=UsiThinkRequest(byoyomi=1000),
            predicted_move=Move.from_usi("7g7f"),
        )

        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()

        def _capture_handler(loop_obj, context) -> None:
            if isinstance(context, dict):
                contexts.append(dict(context))
            if previous_handler is not None:
                previous_handler(loop_obj, context)

        loop.set_exception_handler(_capture_handler)
        try:
            eng._fail_pending(RuntimeError("simulated output end"))  # noqa: SLF001
            eng._bestmove_future = None  # noqa: SLF001
            gc.collect()
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous_handler)

    assert not any("Future exception was never retrieved" in str(ctx.get("message", "")) for ctx in contexts)


@pytest.mark.asyncio
async def test_send_command_rejects_embedded_newline() -> None:
    # A USI command must be a single line; embedded newlines (e.g. from an unsanitised setoption
    # string value) would split into multiple commands. The newline check runs before any
    # instance state is touched, so a bare instance is sufficient to exercise it.
    engine = object.__new__(AsyncUsiEngine)
    with pytest.raises(ValueError):
        await engine._send_command("setoption name X value foo\nquit")
    with pytest.raises(ValueError):
        await engine._send_command("usi\rmalicious")
