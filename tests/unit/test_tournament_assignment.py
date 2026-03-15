import asyncio
import tempfile
from pathlib import Path

import pytest

from shogiarena._core.contexts.tournament.adapters.dashboard_schedule_facade import DashboardScheduleFacade
from shogiarena._core.contexts.tournament.application.runner_state import TournamentRunnerState
from shogiarena._core.contexts.tournament.application.schedule_generation import (
    EngineSpecPort,
    GameScheduler,
    InitialPositionSource,
)
from shogiarena._core.contexts.tournament.application.session.schedule_context import (
    TournamentScheduleContext,
    _ActiveGame,
    _Instance,
    _InstancePool,
    _StopController,
)
from shogiarena._core.contexts.tournament.application.session.schedule_generation_runtime import (
    generate_schedule_for_seed,
)
from shogiarena._core.contexts.tournament.application.session.schedule_helpers import (
    apply_assignment_override,
    serialize_assignment_override,
)
from shogiarena._core.contexts.tournament.application.session.schedule_mutation_service import ScheduleMutationService
from shogiarena._core.contexts.tournament.application.session.schedule_snapshot_service import ScheduleSnapshotService
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.contexts.tournament.ports.session_state_runtime import ScheduleSeed
from shogiarena._core.shared.kernel.json_types import JsonObject


class DummyScheduler(GameScheduler):
    def generate_schedule(
        self,
        engines: list[EngineSpecPort],
        games_per_pair: int,
        seed: ScheduleSeed,
        initial_positions: InitialPositionSource,
    ) -> list[GameSpec]:
        return []

    def get_total_games(self, num_engines: int, games_per_pair: int) -> int:
        return 0


class StaticScheduler(GameScheduler):
    def __init__(self, specs: list[GameSpec]) -> None:
        self._specs = list(specs)

    def generate_schedule(
        self,
        engines: list[EngineSpecPort],
        games_per_pair: int,
        seed: ScheduleSeed,
        initial_positions: InitialPositionSource,
    ) -> list[GameSpec]:
        return list(self._specs)

    def get_total_games(self, num_engines: int, games_per_pair: int) -> int:
        return len(self._specs)


class DummyInitialPositions:
    flip_policy = "none"

    def generate(self, count: int, seed: str) -> list[str]:
        return ["startpos"] * count


class DummyTournamentConfig:
    def __init__(self) -> None:
        self.seed = 42
        self.games_per_pair = 1
        self.game_order = "shuffle"


class DummyRulesConfig:
    def __init__(self) -> None:
        self.initial_positions = DummyInitialPositions()


class DummyEngineSpec:
    def __init__(self, name: str) -> None:
        self.name = name
        self.instance_id = None


class DummyConfig:
    def __init__(self) -> None:
        self.engines = [DummyEngineSpec("EngineA"), DummyEngineSpec("EngineB")]
        self.tournament = DummyTournamentConfig()
        self.rules = DummyRulesConfig()


class DummyStopController:
    def __init__(self) -> None:
        self.is_stop_requested = False
        self.reason: str | None = None

    def request_stop(self, *, reason: str | None = None) -> None:
        self.is_stop_requested = True
        self.reason = reason


class DummyInstance:
    def __init__(self, name: str) -> None:
        self.name = name
        self.type = "local"
        self.active_game_by_id: dict[str, _ActiveGame] = {}


class DummyPool:
    def __init__(self) -> None:
        self._instances = {
            "inst-1": DummyInstance("inst-1"),
            "local": DummyInstance("local"),
        }

    def get_instance(self, name: str) -> _Instance | None:
        return self._instances.get(name)

    def ensure_local_instance(self) -> _Instance:
        return self._instances.setdefault("local", DummyInstance("local"))

    def list_instances(self) -> list[_Instance]:
        return []


def _make_facade_with_schedule(spec: GameSpec) -> DashboardScheduleFacade:
    state = TournamentRunnerState(game_schedule=[spec])
    snapshot = ScheduleSnapshotService()
    mutation = ScheduleMutationService(snapshot_service=snapshot)
    pool: _InstancePool = DummyPool()
    config = DummyConfig()
    stop_ctrl: _StopController = DummyStopController()
    run_dir = Path(tempfile.mkdtemp())

    def make_ctx() -> TournamentScheduleContext:
        return TournamentScheduleContext(
            config=config,
            scheduler=DummyScheduler(),
            instance_pool=pool,
            run_dir=run_dir,
            stop_controller=stop_ctrl,
            is_dashboard_enabled=False,
            engine_instance_defaults={},
            orchestrator=None,
            tournament_orchestrator=None,
            save_run_state=lambda *a, **kw: None,
            update_dashboard=lambda: asyncio.sleep(0),
            reorder_and_shuffle=lambda g: g,
            is_generate_run=lambda: False,
        )

    return DashboardScheduleFacade(
        mutation_service=mutation,
        snapshot_service=snapshot,
        state=state,
        schedule_ctx_supplier=make_ctx,
    )


def test_serialize_assignment_override_shared() -> None:
    spec = GameSpec(
        black_engine="EngineA",
        white_engine="EngineB",
        initial_sfen="startpos",
        game_id="g1",
        assigned_instance_black="inst-1",
        assigned_instance_white="inst-1",
        should_require_install=True,
    )
    payload = serialize_assignment_override(spec)
    assert payload == {
        "black": "inst-1",
        "white": "inst-1",
        "shared": "inst-1",
        "should_require_install": True,
        "mode": "shared",
    }


def test_apply_assignment_override_from_legacy_string() -> None:
    spec = GameSpec(
        black_engine="EngineA",
        white_engine="EngineB",
        initial_sfen="startpos",
        game_id="g1",
    )
    apply_assignment_override(spec, "inst-legacy")
    assert spec.assigned_instance_black == "inst-legacy"
    assert spec.assigned_instance_white == "inst-legacy"
    assert spec.should_require_install is False


@pytest.mark.asyncio
async def test_set_game_instance_per_color_assignment() -> None:
    spec = GameSpec(
        black_engine="EngineA",
        white_engine="EngineB",
        initial_sfen="startpos",
        game_id="g1",
    )
    facade = _make_facade_with_schedule(spec)
    result = await facade.set_game_instance(
        "g1",
        mode="per_color",
        black_instance="inst-1",
        white_instance=None,
        should_require_install=True,
    )
    assert spec.assigned_instance_black == "inst-1"
    assert spec.assigned_instance_white is None
    assert spec.should_require_install is True
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
        should_require_install=True,
    )
    facade = _make_facade_with_schedule(spec)
    await facade.set_game_instance("g1", mode="auto")
    assert spec.assigned_instance_black is None
    assert spec.assigned_instance_white is None
    assert spec.should_require_install is False


def test_generate_schedule_for_seed_does_not_mutate_config_seed() -> None:
    specs = [
        GameSpec(black_engine="EngineA", white_engine="EngineB", initial_sfen="startpos", game_id=f"g{idx}")
        for idx in range(4)
    ]
    scheduler = StaticScheduler(specs)
    config = DummyConfig()
    original_seed = config.tournament.seed
    stop_ctrl: _StopController = DummyStopController()

    ctx = TournamentScheduleContext(
        config=config,
        scheduler=scheduler,
        instance_pool=DummyPool(),
        run_dir=Path(tempfile.mkdtemp()),
        stop_controller=stop_ctrl,
        is_dashboard_enabled=False,
        engine_instance_defaults={},
        reorder_and_shuffle=lambda g: list(g),
        is_generate_run=lambda: False,
    )

    first = generate_schedule_for_seed(ctx, seed="12345")
    second = generate_schedule_for_seed(ctx, seed="12345")

    assert config.tournament.seed == original_seed
    assert [spec.game_id for spec in first] == [spec.game_id for spec in second]


@pytest.mark.asyncio
async def test_request_reschedule_uses_injected_snapshot_service() -> None:
    class FakeSnapshotService:
        def __init__(self) -> None:
            self.calls = 0

        async def get_schedule_snapshot(
            self, state: TournamentRunnerState, ctx: TournamentScheduleContext
        ) -> JsonObject:
            self.calls += 1
            return {"pending_games": 7}

    existing_specs = [
        GameSpec(black_engine="EngineA", white_engine="EngineB", initial_sfen="startpos", game_id="g1"),
        GameSpec(black_engine="EngineA", white_engine="EngineB", initial_sfen="startpos", game_id="g2"),
    ]
    generated_specs = [
        GameSpec(black_engine="EngineA", white_engine="EngineB", initial_sfen="startpos", game_id="g3"),
        GameSpec(black_engine="EngineA", white_engine="EngineB", initial_sfen="startpos", game_id="g4"),
    ]
    state = TournamentRunnerState(game_schedule=list(existing_specs))
    state.completed_game_ids.add("g1")
    snapshot = FakeSnapshotService()
    mutation = ScheduleMutationService(snapshot_service=snapshot)
    config = DummyConfig()
    stop_ctrl: _StopController = DummyStopController()

    ctx = TournamentScheduleContext(
        config=config,
        scheduler=StaticScheduler(generated_specs),
        instance_pool=DummyPool(),
        run_dir=Path(tempfile.mkdtemp()),
        stop_controller=stop_ctrl,
        is_dashboard_enabled=False,
        engine_instance_defaults={},
        orchestrator=None,
        tournament_orchestrator=None,
        save_run_state=lambda *a, **kw: None,
        update_dashboard=lambda: asyncio.sleep(0),
        reorder_and_shuffle=lambda g: list(g),
        is_generate_run=lambda: False,
    )

    result = await mutation.request_reschedule(state, ctx, seed="12345")

    assert result["status"] == "applied"
    assert result["pending_games"] == 7
    assert snapshot.calls == 1
