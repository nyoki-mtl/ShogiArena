import json
import textwrap
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
import rshogi
from rshogi.initial_positions import InitialPosition

from shogiarena.arena.configs.spsa import load_config_yaml
from shogiarena.arena.orchestrators.base_orchestrator_utils import numeric_game_id
from shogiarena.arena.orchestrators.spsa_orchestrator import SpsaOrchestrator
from shogiarena.arena.session import GameCompletionEvent, LifecycleHooksBase, SessionContext
from shogiarena.arena.storage import FilesystemRunStorage
from shogiarena.arena.tuning.param_io import read_params
from shogiarena.utils.types.types import GameResult


class _CapturingHooks(LifecycleHooksBase):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[GameCompletionEvent] = []

    async def on_game_complete(self, event: GameCompletionEvent) -> None:
        self.events.append(event)


class _DummyRng:
    """Deterministic RNG matching the interface used by SpsaOrchestrator."""

    def randint(self, _a: int, _b: int) -> int:
        return 1  # Always pick +step

    def randrange(self, _n: int) -> int:
        return 0

    def random(self) -> float:
        return 0.0


@pytest.mark.asyncio
async def test_spsa_update_matches_reference_script(tmp_path: Path) -> None:
    """Verify SPSA updates follow the BloodgateSPSA (YaneuraOu) semantics."""

    def write(rel: str, content: str) -> Path:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    engine_cfg = write(
        "cfg/engine.yaml",
        """
        engine_path: "/bin/echo"
        options:
          Threads: 1
        """,
    )
    sfens = write("cfg/sfens.txt", "startpos\n")
    params_path = write("cfg/spsa.params", "param1, float, 10.0, 0.0, 20.0, 2.0, 0.5\n")

    config_yaml = write(
        "cfg/spsa.yaml",
        f"""
        experiment_name: exp
        engines:
          - engine_path: "{engine_cfg}"
            name: tuned
        rules:
          time_control:
            time_ms: 60000
            byoyomi_ms: 1000
          initial_positions:
            type: file
            source: "{sfens}"
        spsa:
          parameters_path: "{params_path}"
          num_updates: 1
          num_parallel: 1
          mobility: 1.0
          scale: 1.0
          alpha: 0.0
          gamma: 0.0
          A: 0.0
        """,
    )

    cfg = load_config_yaml(str(config_yaml))
    storage = FilesystemRunStorage(tmp_path)
    session = SessionContext.build(storage=storage, num_workers=1, run_id="test")
    orch = SpsaOrchestrator(cfg, session=session, hooks=LifecycleHooksBase())
    now = datetime.now(timezone.utc).isoformat()
    state = {
        "type": "spsa",
        "created_at": now,
        "updated_at": now,
        "finished": False,
        "completed_updates": 0,
        "total_updates": 1,
    }
    (tmp_path / "run_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    params = read_params(params_path)
    orch.set_update_items([0], params, ["startpos"])

    # Make perturbation deterministic and provide scripted match outcomes.
    orch._make_rng = lambda _idx: _DummyRng()  # type: ignore[assignment]
    scores: Iterator[float] = iter([1.0, -1.0])

    async def fake_run_game_pair(*_args, **_kwargs):
        return (next(scores), None, None)

    orch._run_game_pair = fake_run_game_pair  # type: ignore[assignment]

    await orch._run_one_spsa_update(0)

    # Expect +1.0 shift in parameter value: 10.0 -> 11.0
    updated_params = read_params(params_path)
    assert pytest.approx(updated_params[0].v, abs=1e-6) == 11.0


@pytest.mark.asyncio
async def test_spsa_completion_reports_assigned_worker(tmp_path: Path) -> None:
    def write(rel: str, content: str) -> Path:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(textwrap.dedent(content), encoding="utf-8")
        return dest

    engine_cfg = write(
        "cfg/engine.yaml",
        """
        engine_path: "/bin/echo"
        options:
          Threads: 1
        """,
    )
    sfens = write("cfg/sfens.txt", "startpos\n")
    params_path = write("cfg/spsa.params", "param1, float, 10.0, 0.0, 20.0, 2.0, 0.5\n")
    config_yaml = write(
        "cfg/spsa.yaml",
        f"""
        experiment_name: exp
        engines:
          - engine_path: "{engine_cfg}"
            name: tuned
        rules:
          time_control:
            time_ms: 60000
            byoyomi_ms: 1000
          initial_positions:
            type: file
            source: "{sfens}"
        spsa:
          parameters_path: "{params_path}"
          num_updates: 1
          num_parallel: 1
          mobility: 1.0
          scale: 1.0
          inflight_factor: 1
        """,
    )

    cfg = load_config_yaml(str(config_yaml))
    storage = FilesystemRunStorage(tmp_path)
    session = SessionContext.build(storage=storage, num_workers=2, run_id="test")
    hooks = _CapturingHooks()
    orch = SpsaOrchestrator(cfg, session=session, hooks=hooks)
    params = read_params(params_path)
    orch.set_update_items([0], params, ["startpos"])

    async def fake_execute_game(self, spec):  # type: ignore[no-untyped-def]
        return rshogi.record.GameRecord.from_dict(
            {
                "metadata": {
                    "game_name": spec.game_id,
                    "game_type": "spsa",
                    "black_player": "b",
                    "white_player": "w",
                    "attributes": {"game_name": spec.game_id, "game_type": "spsa"},
                },
                "init_position_sfen": InitialPosition.STANDARD.value,
                "moves": [],
                "result": {"result": GameResult.DRAW_BY_REPETITION.name, "ply_count": 0},
            }
        )

    orch._execute_game = fake_execute_game.__get__(orch, SpsaOrchestrator)  # type: ignore[assignment]

    await orch._run_single_spsa_game(
        start_sfen="startpos",
        tuned_params=params,
        current_params=params,
        worker_idx=1,  # intentionally differs from actual slot the orchestrator will pick (0)
        tuned_as_black=True,
        update_idx=0,
        phase="plus",
        preassigned_game_id=None,
    )

    assert hooks.events, "expected SPSA completion event"
    assert hooks.events[-1].worker_idx == 0

    await orch.shutdown()


@pytest.mark.asyncio
async def test_spsa_completion_uses_actual_worker_after_deferred_assignment(tmp_path: Path) -> None:
    def write(rel: str, content: str) -> Path:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(textwrap.dedent(content), encoding="utf-8")
        return dest

    engine_cfg = write(
        "cfg/engine.yaml",
        """
        engine_path: "/bin/echo"
        options:
          Threads: 1
        """,
    )
    sfens = write("cfg/sfens.txt", "startpos\n")
    params_path = write("cfg/spsa.params", "param1, float, 10.0, 0.0, 20.0, 2.0, 0.5\n")
    config_yaml = write(
        "cfg/spsa.yaml",
        f"""
        experiment_name: exp
        engines:
          - engine_path: "{engine_cfg}"
            name: tuned
        rules:
          time_control:
            time_ms: 60000
            byoyomi_ms: 1000
          initial_positions:
            type: file
            source: "{sfens}"
        spsa:
          parameters_path: "{params_path}"
          num_updates: 1
          num_parallel: 1
          mobility: 1.0
          scale: 1.0
          inflight_factor: 1
        """,
    )

    cfg = load_config_yaml(str(config_yaml))
    storage = FilesystemRunStorage(tmp_path)
    session = SessionContext.build(storage=storage, num_workers=2, run_id="test")
    hooks = _CapturingHooks()
    orch = SpsaOrchestrator(cfg, session=session, hooks=hooks)
    params = read_params(params_path)
    orch.set_update_items([0], params, ["startpos"])

    # Force preassignment failure so that initial resolved worker is None
    orch.worker_busy = {0, 1}

    async def fake_execute_game(self, spec):  # type: ignore[no-untyped-def]
        orch.game_to_worker[numeric_game_id(spec.game_id)] = 1
        return rshogi.record.GameRecord.from_dict(
            {
                "metadata": {
                    "game_name": spec.game_id,
                    "game_type": "spsa",
                    "black_player": "b",
                    "white_player": "w",
                    "attributes": {"game_name": spec.game_id, "game_type": "spsa"},
                },
                "init_position_sfen": InitialPosition.STANDARD.value,
                "moves": [],
                "result": {"result": GameResult.DRAW_BY_REPETITION.name, "ply_count": 0},
            }
        )

    orch._execute_game = fake_execute_game.__get__(orch, SpsaOrchestrator)  # type: ignore[assignment]

    await orch._run_single_spsa_game(
        start_sfen="startpos",
        tuned_params=params,
        current_params=params,
        worker_idx=7,
        tuned_as_black=True,
        update_idx=0,
        phase="plus",
        preassigned_game_id="test-game",
    )

    assert hooks.events, "expected SPSA completion event"
    assert hooks.events[-1].worker_idx == 1

    await orch.shutdown()
