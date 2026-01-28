import asyncio

import pytest

from shogiarena.arena.engines.usi_config import UsiEngineConfig
from shogiarena.arena.engines.usi_engine import AsyncUsiEngine
from shogiarena.arena.engines.usi_protocol import UsiOption


class DummyBridge:
    def __init__(self, name: str = "dummy") -> None:
        self._name = name
        self.sent: list[str] = []

    @property
    def name(self) -> str:  # noqa: D401
        return self._name

    async def start_process(self) -> None:  # noqa: D401
        return None

    async def stop_process(self) -> None:  # noqa: D401
        return None

    async def send_line(self, command: str) -> None:  # noqa: D401
        self.sent.append(command)

    async def _empty_async_iter(self):  # type: ignore[return-value]
        return
        yield  # pragma: no cover

    def receive_lines(self):  # noqa: D401
        return self._empty_async_iter()

    def is_running(self) -> bool:  # noqa: D401
        return True


@pytest.mark.asyncio
async def test_handle_info_updates_latest_pv() -> None:
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    eng = AsyncUsiEngine(config=config, bridge=DummyBridge("E"))

    # Metrics-only info should set _latest_pv
    await eng._handle_line("info depth 10 nodes 123 time 45")
    latest = eng._current_pvs[1]
    assert latest.depth == 10
    assert latest.nodes == 123
    assert latest.time == 45

    # String-only info should not discard the previous metrics entry
    prev = eng._current_pvs[1]
    await eng._handle_line("info string hello world")
    assert eng._current_pvs[1] is prev
    assert eng._current_aux_info and eng._current_aux_info[-1].string == "hello world"


@pytest.mark.asyncio
async def test_bestmove_future_resolves_after_go() -> None:
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    eng = AsyncUsiEngine(config=config, bridge=DummyBridge("E"))

    loop = asyncio.get_running_loop()
    eng._bestmove_future = loop.create_future()
    await eng._handle_line("info depth 18 nodes 456 time 90")
    await eng._handle_line("info string auxiliary info")
    await eng._handle_line("bestmove 7g7f ponder 3c3d")
    res = await eng._bestmove_future
    assert res.bestmove == "7g7f"
    assert res.ponder == "3c3d"
    assert res.pvs and len(res.pvs) == 1
    latest = res.pvs[0]
    assert latest.depth == 18 and latest.nodes == 456 and latest.time == 90
    assert latest.string is None


@pytest.mark.asyncio
async def test_ready_and_handshake_signals() -> None:
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    eng = AsyncUsiEngine(config=config, bridge=DummyBridge("E"))

    loop = asyncio.get_running_loop()
    usi_future = loop.create_future()
    eng._usiok_future = usi_future
    await eng._handle_line("usiok")
    assert usi_future.done()

    ready_future = loop.create_future()
    eng._readyok_future = ready_future
    await eng._handle_line("readyok")
    assert ready_future.done()


@pytest.mark.asyncio
async def test_handle_line_ignores_custom_updates() -> None:
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    eng = AsyncUsiEngine(config=config, bridge=DummyBridge("E"))
    # Should not raise despite unknown prefix
    await eng._handle_line("update_quiet_histories_1,771,0")


@pytest.mark.asyncio
async def test_handle_line_propagates_error() -> None:
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    eng = AsyncUsiEngine(config=config, bridge=DummyBridge("E"))
    with pytest.raises(RuntimeError):
        await eng._handle_line("Error! : failed to read nn.bin : FileNotFound")


@pytest.mark.asyncio
async def test_missing_option_raises_runtime_error() -> None:
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy", "options": {"DrawPly": 24}})
    eng = AsyncUsiEngine(config=config, bridge=DummyBridge("E"))
    eng._handshake_timeout = 0.01
    # Pretend handshake completed so options dictionary empty -> option missing
    with pytest.raises(RuntimeError):
        await eng._set_option("DrawPly", 24)


@pytest.mark.asyncio
async def test_option_candidates_apply_to_available_option() -> None:
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    bridge = DummyBridge("E")
    eng = AsyncUsiEngine(config=config, bridge=bridge)
    eng._options["MaxMovesToDraw"] = UsiOption(name="MaxMovesToDraw", option_type="spin")
    eng._process._running = True  # type: ignore[attr-defined] - priming for direct send
    await eng._set_option("DrawPly|MaxMovesToDraw", 128)
    assert bridge.sent[-1] == "setoption name MaxMovesToDraw value 128"


@pytest.mark.asyncio
async def test_option_candidates_apply_to_all_matches() -> None:
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    bridge = DummyBridge("E")
    eng = AsyncUsiEngine(config=config, bridge=bridge)
    eng._options["MaxMovesToDraw"] = UsiOption(name="MaxMovesToDraw", option_type="spin")
    eng._options["DrawPly"] = UsiOption(name="DrawPly", option_type="spin")
    eng._process._running = True  # type: ignore[attr-defined]
    await eng._set_option("DrawPly|MaxMovesToDraw", 320)
    assert bridge.sent[-2:] == [
        "setoption name DrawPly value 320",
        "setoption name MaxMovesToDraw value 320",
    ]


def test_engine_args_requires_iterable() -> None:
    with pytest.raises(TypeError):
        UsiEngineConfig.from_mapping(
            {
                "name": "test",
                "engine_path": "/tmp/dummy",
                "engine_args": "--nnue",
            }
        )


def test_env_requires_mapping() -> None:
    with pytest.raises(TypeError):
        UsiEngineConfig.from_mapping(
            {
                "name": "test",
                "engine_path": "/tmp/dummy",
                "env": ["INVALID"],
            }
        )


def test_overrides_require_mapping() -> None:
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    with pytest.raises(TypeError):
        config.with_overrides(options=[("Threads", 2)])
