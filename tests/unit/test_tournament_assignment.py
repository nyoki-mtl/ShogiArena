import asyncio
from types import SimpleNamespace

import pytest

from shogiarena.arena.configs.tournament import GameSpec
from shogiarena.arena.runners.tournament_runner import TournamentRunner
from shogiarena.arena.storage import TempRunStorage


class DummyPool:
    def __init__(self) -> None:
        self._instances = {"inst-1": object(), "local": object()}

    def get_instance(self, name: str) -> object | None:
        return self._instances.get(name)

    def ensure_local_instance(self) -> object:
        return self._instances.setdefault("local", object())

    def list_instances(self) -> list[object]:
        return []


def make_runner_with_schedule(spec: GameSpec) -> TournamentRunner:
    runner = object.__new__(TournamentRunner)
    runner._storage = TempRunStorage()
    runner.run_dir = runner._storage.run_dir
    runner.instance_pool = DummyPool()
    runner._reschedule_lock = asyncio.Lock()
    runner._dashboard_enabled = False
    runner.completed_game_ids = set()
    runner.cancelled_game_ids = set()
    runner.game_schedule = [spec]
    runner._cancelled_specs = {}
    runner._refresh_game_assignments = lambda: None  # type: ignore[attr-defined]
    runner._write_schedule_file = lambda _schedule: None  # type: ignore[attr-defined]
    runner._save_run_state = lambda: None  # type: ignore[attr-defined]

    async def _noop_update() -> None:  # noqa: ANN201
        return None

    runner._update_dashboard = _noop_update  # type: ignore[attr-defined]
    runner.config = SimpleNamespace(
        engines=[
            SimpleNamespace(name="EngineA", instance_id=None),
            SimpleNamespace(name="EngineB", instance_id=None),
        ],
        dashboard=SimpleNamespace(enabled=False),
    )
    runner._stop_controller = SimpleNamespace(stop_requested=False, reason=None)
    return runner  # type: ignore[return-value]


def test_serialize_assignment_override_shared() -> None:
    spec = GameSpec(
        black_engine="EngineA",
        white_engine="EngineB",
        initial_sfen="startpos",
        game_id="g1",
        assigned_instance_black="inst-1",
        assigned_instance_white="inst-1",
        require_install=True,
    )
    runner = object.__new__(TournamentRunner)
    payload = runner._serialize_assignment_override(spec)  # type: ignore[attr-defined]
    assert payload == {
        "black": "inst-1",
        "white": "inst-1",
        "shared": "inst-1",
        "require_install": True,
        "mode": "shared",
    }


def test_apply_assignment_override_from_legacy_string() -> None:
    spec = GameSpec(
        black_engine="EngineA",
        white_engine="EngineB",
        initial_sfen="startpos",
        game_id="g1",
    )
    runner = object.__new__(TournamentRunner)
    runner._apply_assignment_override(spec, "inst-legacy")  # type: ignore[attr-defined]
    assert spec.assigned_instance_black == "inst-legacy"
    assert spec.assigned_instance_white == "inst-legacy"
    assert spec.require_install is False


@pytest.mark.asyncio
async def test_set_game_instance_per_color_assignment() -> None:
    spec = GameSpec(
        black_engine="EngineA",
        white_engine="EngineB",
        initial_sfen="startpos",
        game_id="g1",
    )
    runner = make_runner_with_schedule(spec)
    result = await runner.set_game_instance(
        "g1",
        mode="per_color",
        black_instance="inst-1",
        white_instance=None,
        require_install=True,
    )
    assert spec.assigned_instance_black == "inst-1"
    assert spec.assigned_instance_white is None
    assert spec.require_install is True
    assert result["mode"] == "per_color"
    assert result["black_instance"] == "inst-1"
    assert result["white_instance"] is None


@pytest.mark.asyncio
async def test_set_game_instance_auto_clears_overrides() -> None:
    spec = GameSpec(
        black_engine="EngineA",
        white_engine="EngineB",
        initial_sfen="startpos",
        game_id="g1",
        assigned_instance_black="inst-1",
        assigned_instance_white="inst-1",
        require_install=True,
    )
    runner = make_runner_with_schedule(spec)
    await runner.set_game_instance("g1", mode="auto")
    assert spec.assigned_instance_black is None
    assert spec.assigned_instance_white is None
    assert spec.require_install is False
