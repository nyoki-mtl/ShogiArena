import asyncio

import pytest
from rshogi.core import Move

from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.platform.engine_runtime.usi_engine_session import AsyncUsiEngine, UsiEngineState
from shogiarena._core.platform.engine_runtime.usi_protocol_types import UsiOption


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
    eng._state = UsiEngineState.WAITING_FOR_BESTMOVE
    eng._bestmove_future = loop.create_future()
    await eng._handle_line("info depth 18 nodes 456 time 90")
    await eng._handle_line("info string auxiliary info")
    await eng._handle_line("bestmove 7g7f ponder 3c3d")
    res = await asyncio.wait_for(asyncio.shield(eng._bestmove_future), timeout=0.1)
    assert res.bestmove == Move.from_usi("7g7f")
    assert res.ponder == Move.from_usi("3c3d")
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
    eng._process._is_running = True  # type: ignore[attr-defined] - priming for direct send
    await eng._set_option("DrawPly|MaxMovesToDraw", 128)
    assert bridge.sent[-1] == "setoption name MaxMovesToDraw value 128"


@pytest.mark.asyncio
async def test_option_candidates_apply_to_all_matches() -> None:
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    bridge = DummyBridge("E")
    eng = AsyncUsiEngine(config=config, bridge=bridge)
    eng._options["MaxMovesToDraw"] = UsiOption(name="MaxMovesToDraw", option_type="spin")
    eng._options["DrawPly"] = UsiOption(name="DrawPly", option_type="spin")
    eng._process._is_running = True  # type: ignore[attr-defined]
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


def test_environment_requires_mapping() -> None:
    with pytest.raises(TypeError):
        UsiEngineConfig.from_mapping(
            {
                "name": "test",
                "engine_path": "/tmp/dummy",
                "environment": ["INVALID"],
            }
        )


def test_overrides_require_mapping() -> None:
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    with pytest.raises(TypeError):
        config.with_overrides(options=[("Threads", 2)])


# -- info string collection tests --


@pytest.mark.asyncio
async def test_info_string_collection_disabled_by_default() -> None:
    """should_collect_info_strings がデフォルトで無効であることを確認する。"""
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    eng = AsyncUsiEngine(config=config, bridge=DummyBridge("E"))

    assert eng._should_collect_info_strings is False
    assert eng._info_string_log == []

    await eng._handle_line("info string debug msg 1")
    # 無効時は _info_string_log に追加されない
    assert eng._info_string_log == []


@pytest.mark.asyncio
async def test_info_string_collection_when_enabled() -> None:
    """should_collect_info_strings 有効時に info string が蓄積されることを確認する。"""
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    eng = AsyncUsiEngine(config=config, bridge=DummyBridge("E"), should_collect_info_strings=True)

    await eng._handle_line("info string hello world")
    await eng._handle_line("info string debug msg 2")
    await eng._handle_line("info depth 10 nodes 100")  # not a string-only info

    assert eng._info_string_log == ["hello world", "debug msg 2"]


@pytest.mark.asyncio
async def test_info_string_log_cleared_on_reset() -> None:
    """_reset_current_info で _info_string_log もクリアされることを確認する。"""
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    eng = AsyncUsiEngine(config=config, bridge=DummyBridge("E"), should_collect_info_strings=True)

    await eng._handle_line("info string before reset")
    assert eng._info_string_log == ["before reset"]

    eng._reset_current_info()
    assert eng._info_string_log == []

    await eng._handle_line("info string after reset")
    assert eng._info_string_log == ["after reset"]


@pytest.mark.asyncio
async def test_info_strings_attached_to_bestmove_result() -> None:
    """bestmove 結果に info_strings が付与されることを確認する。"""
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    eng = AsyncUsiEngine(config=config, bridge=DummyBridge("E"), should_collect_info_strings=True)

    loop = asyncio.get_running_loop()
    eng._bestmove_future = loop.create_future()
    eng._state = UsiEngineState.WAITING_FOR_BESTMOVE

    await eng._handle_line("info string debug line 1")
    await eng._handle_line("info depth 10 score cp 100")
    await eng._handle_line("info string debug line 2")
    await eng._handle_line("bestmove 7g7f ponder 3c3d")

    result = eng._bestmove_future.result()
    assert result.bestmove == Move.from_usi("7g7f")
    assert result.info_strings == ("debug line 1", "debug line 2")
    # reset 後は空
    assert eng._info_string_log == []


@pytest.mark.asyncio
async def test_info_strings_attached_to_checkmate_result() -> None:
    """checkmate 結果に info_strings が付与されることを確認する。"""
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    eng = AsyncUsiEngine(config=config, bridge=DummyBridge("E"), should_collect_info_strings=True)

    loop = asyncio.get_running_loop()
    eng._mate_future = loop.create_future()
    eng._state = UsiEngineState.WAITING_FOR_CHECKMATE

    await eng._handle_line("info string mate search started")
    await eng._handle_line("info string depth 5 found")
    await eng._handle_line("checkmate 7g7f 3c3d 8h2b+")

    result = eng._mate_future.result()
    assert result.is_mate is True
    assert result.moves == (Move.from_usi("7g7f"), Move.from_usi("3c3d"), Move.from_usi("8h2b+"))
    assert result.info_strings == ("mate search started", "depth 5 found")


@pytest.mark.asyncio
async def test_info_strings_attached_to_nomate_result() -> None:
    """nomate 結果に info_strings が付与されることを確認する。"""
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    eng = AsyncUsiEngine(config=config, bridge=DummyBridge("E"), should_collect_info_strings=True)

    loop = asyncio.get_running_loop()
    eng._mate_future = loop.create_future()
    eng._state = UsiEngineState.WAITING_FOR_CHECKMATE

    await eng._handle_line("info string searching...")
    await eng._handle_line("checkmate nomate")

    result = eng._mate_future.result()
    assert result.is_mate is False
    assert result.info_strings == ("searching...",)


@pytest.mark.asyncio
async def test_info_strings_attached_to_timeout_result() -> None:
    """timeout 結果に info_strings が付与されることを確認する。"""
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    eng = AsyncUsiEngine(config=config, bridge=DummyBridge("E"), should_collect_info_strings=True)

    loop = asyncio.get_running_loop()
    eng._mate_future = loop.create_future()
    eng._state = UsiEngineState.WAITING_FOR_CHECKMATE

    await eng._handle_line("info string timeout debug msg")
    await eng._handle_line("checkmate timeout")

    result = eng._mate_future.result()
    assert result.is_mate is False
    assert result.info_strings == ("timeout debug msg",)


@pytest.mark.asyncio
async def test_info_strings_empty_when_disabled() -> None:
    """should_collect_info_strings 無効時は結果の info_strings が空であることを確認する。"""
    config = UsiEngineConfig.from_mapping({"name": "test", "engine_path": "/tmp/dummy"})
    eng = AsyncUsiEngine(config=config, bridge=DummyBridge("E"))

    loop = asyncio.get_running_loop()
    eng._bestmove_future = loop.create_future()
    eng._state = UsiEngineState.WAITING_FOR_BESTMOVE

    await eng._handle_line("info string debug msg")
    await eng._handle_line("bestmove 7g7f")

    result = eng._bestmove_future.result()
    assert result.info_strings == ()
