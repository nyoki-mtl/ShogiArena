from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.game_execution import execute_game


class _EngineItem:
    pool_key = "engine-a"
    config_path = Path("engine-a.yaml")
    extra_options = None
    instance_override = None


class _GameSpec:
    black_item = _EngineItem()
    white_item = _EngineItem()
    initial_sfen = "startpos"
    game_id = "g-cancelled"
    black_limits = None
    white_limits = None
    before_game_hook = None
    game_round = None
    on_game_start = None


class _EnginePool:
    async def acquire_pair_sorted(self, *_args: object, **_kwargs: object) -> tuple[object, object]:
        raise asyncio.CancelledError()


class _Owner:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.engine_pool = _EnginePool()
        self.game_runner = object()
        self.instance_pool = None


@pytest.mark.asyncio
async def test_execute_game_records_user_interruption(tmp_path: Path) -> None:
    owner = _Owner(tmp_path / "run")

    with pytest.raises(asyncio.CancelledError):
        await execute_game(owner, _GameSpec())

    payload = json.loads((owner.run_dir / "failures" / "run_failures.json").read_text(encoding="utf-8"))
    assert payload["failures"][0]["failure_phase"] == "user_interruption"
    assert payload["failures"][0]["game_id"] == "g-cancelled"
