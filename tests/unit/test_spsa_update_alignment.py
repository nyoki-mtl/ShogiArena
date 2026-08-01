import json
import textwrap
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import rsshogi
from rsshogi.initial_positions import InitialPosition

from shogiarena._core.contexts.game_session.adapters.run_storage import FilesystemRunStorage
from shogiarena._core.contexts.game_session.application.orchestration.concurrent_executor import numeric_game_id
from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.spsa.adapters import orchestrator_update_flow
from shogiarena._core.contexts.spsa.adapters.orchestrator import SpsaOrchestrator
from shogiarena._core.contexts.spsa.application.space_spec import load_spsa_space_spec
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.participation_records import GameParticipationRecord
from shogiarena._core.shared.kernel.session_hooks import GameCompletionEvent, NoopGameLifecycleHooks
from tests.unit.spsa_config_test_helpers import load_spsa_run_config

_mock_engine_factory_service = EngineFactoryService(factory=AsyncMock())


class _CapturingHooks(NoopGameLifecycleHooks):
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


class _SharedStochasticRng(_DummyRng):
    def random(self) -> float:
        return 0.2


def _write_space(write, *, initial: float = 10.0) -> Path:
    return write(
        "cfg/space.yaml",
        f"""
        schema_version: shogiarena.spsa.space.v1
        target:
          engine_family: test
          protocol: usi_options
          required_options_policy: strict
          tunable_manifest:
            required: false
            command: usi_tunables
        parameters:
          - id: param1
            target:
              option: param1
              value_encoding: decimal
            value_type: float
            initial: {initial}
            bounds:
              min: 0.0
              max: 20.0
            schedule:
              c_end: 2.0
              r_end: 0.5
        """,
    )


def _write_int_space(write, *, initial: int = 10) -> Path:
    return write(
        "cfg/space.yaml",
        f"""
        schema_version: shogiarena.spsa.space.v1
        target:
          engine_family: test
          protocol: usi_options
          required_options_policy: strict
          tunable_manifest:
            required: false
            command: usi_tunables
        parameters:
          - id: visits
            target:
              option: Tune.Visits
              value_encoding: integer
            value_type: int
            initial: {initial}
            bounds:
              min: 0
              max: 20
            schedule:
              c_end: 0.5
              r_end: 0.1
            rounding:
              mode: stochastic
        """,
    )


@pytest.mark.asyncio
async def test_spsa_update_matches_reference_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify direct plus/minus SPSA update math with a deterministic score."""

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
    space_path = _write_space(write)

    config_yaml = write(
        "cfg/spsa.yaml",
        f"""
        experiment_name: exp
        engines:
          - engine_path: {json.dumps(str(engine_cfg))}
            name: tuned
        rules:
          time_control:
            time_ms: 60000
            byoyomi_ms: 1000
          initial_positions:
            type: file
            source: {json.dumps(str(sfens))}
        spsa:
          space: {json.dumps(str(space_path))}
          num_updates: 1
          pairs_per_update: 1
          num_parallel: 1
          algorithm:
            name: classic
            alpha: 0.602
            gamma: 0.101
            A:
              mode: absolute
              value: 0.0
          variants:
            pairing: plus_minus
            crn: true
            integer_rounding: none
        """,
    )

    cfg = load_spsa_run_config(config_yaml)
    cfg.run_seed = "00" * 32
    storage = FilesystemRunStorage(tmp_path)
    session = SessionContext.build(storage=storage, num_workers=1, run_id="test")
    orch = SpsaOrchestrator(
        cfg,
        session=session,
        hooks=NoopGameLifecycleHooks(),
        engine_factory_service=_mock_engine_factory_service,
        ledger_runtime=Mock(game_result_kind=Mock(return_value=None)),
    )
    now = datetime.now(UTC).isoformat()
    state = {
        "type": "spsa",
        "created_at": now,
        "updated_at": now,
        "is_finished": False,
        "completed_updates": 0,
        "total_updates": 1,
    }
    (tmp_path / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    params = load_spsa_space_spec(space_path).to_param_entries()
    orch.set_work_items([1], params, ["startpos"])

    # Make perturbation deterministic and provide scripted match outcomes.
    orch._make_rng = lambda **_kwargs: _DummyRng()  # type: ignore[assignment]
    scores: Iterator[float] = iter([0.5])

    async def fake_run_game_pair(*_args, **_kwargs):
        return (next(scores), None, None)

    orch._run_game_pair = fake_run_game_pair
    accepted_path = tmp_path / "spsa" / "accepted-best.json"

    def fake_persist_accepted_best(**kwargs: object) -> Path:
        accepted_path.parent.mkdir(parents=True, exist_ok=True)
        accepted_path.write_text(
            json.dumps({"wire_values": kwargs["parameter_wire_values"]}),
            encoding="utf-8",
        )
        return accepted_path

    monkeypatch.setattr(
        orchestrator_update_flow,
        "persist_accepted_best",
        fake_persist_accepted_best,
    )

    await orch._run_one_spsa_update(1)

    # Direct pairing converts a pair average of +0.5 into score_sum=+1.0.
    assert pytest.approx(orch._params[0].value, abs=1e-6) == 11.0
    accepted = json.loads(accepted_path.read_text(encoding="utf-8"))
    assert accepted["wire_values"] == {"param1": "11"}


@pytest.mark.asyncio
async def test_spsa_int_stochastic_options_are_assigned_once_and_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    space_path = _write_int_space(write)
    config_yaml = write(
        "cfg/spsa.yaml",
        f"""
        experiment_name: exp
        engines:
          - engine_path: {json.dumps(str(engine_cfg))}
            name: tuned
        rules:
          time_control:
            node_limit: 1
          initial_positions:
            type: file
            source: {json.dumps(str(sfens))}
        spsa:
          space: {json.dumps(str(space_path))}
          num_updates: 1
          pairs_per_update: 1
          num_parallel: 1
          algorithm:
            name: classic
            alpha: 0.602
            gamma: 0.101
            A:
              mode: absolute
              value: 0.0
          variants:
            pairing: plus_minus
            crn: true
            integer_rounding: stochastic
        """,
    )

    cfg = load_spsa_run_config(config_yaml)
    cfg.run_seed = "00" * 32
    storage = FilesystemRunStorage(tmp_path)
    session = SessionContext.build(storage=storage, num_workers=1, run_id="test")
    orch = SpsaOrchestrator(
        cfg,
        session=session,
        hooks=NoopGameLifecycleHooks(),
        engine_factory_service=_mock_engine_factory_service,
        ledger_runtime=Mock(game_result_kind=Mock(return_value=None)),
    )
    now = datetime.now(UTC).isoformat()
    state = {
        "type": "spsa",
        "created_at": now,
        "updated_at": now,
        "is_finished": False,
        "completed_updates": 0,
        "total_updates": 1,
    }
    (tmp_path / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    params = load_spsa_space_spec(space_path).to_param_entries()
    orch.set_work_items([1], params, ["startpos"])
    orch._make_rng = lambda **_kwargs: _SharedStochasticRng()  # type: ignore[assignment]
    captured_options: list[tuple[dict[str, object], dict[str, object]]] = []

    async def fake_run_game_pair(*_args, **kwargs):
        captured_options.append((dict(kwargs["tuned_option_map"]), dict(kwargs["baseline_option_map"])))
        return (0.0, None, None)

    orch._run_game_pair = fake_run_game_pair
    monkeypatch.setattr(
        orchestrator_update_flow,
        "persist_accepted_best",
        lambda **_kwargs: tmp_path / "spsa" / "accepted-best.json",
    )

    await orch._run_one_spsa_update(1)

    variant_line = (tmp_path / "spsa" / "variants.jsonl").read_text(encoding="utf-8").strip()
    variant_payload = json.loads(variant_line)
    plus_options = variant_payload["variants"]["plus"]["applied_options"]
    minus_options = variant_payload["variants"]["minus"]["applied_options"]

    assert plus_options == {"Tune.Visits": 10}
    assert minus_options == {"Tune.Visits": 9}
    assert captured_options == [(plus_options, minus_options)]


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
    space_path = _write_space(write)
    config_yaml = write(
        "cfg/spsa.yaml",
        f"""
        experiment_name: exp
        engines:
          - engine_path: {json.dumps(str(engine_cfg))}
            name: tuned
        rules:
          time_control:
            time_ms: 60000
            byoyomi_ms: 1000
          initial_positions:
            type: file
            source: {json.dumps(str(sfens))}
        spsa:
          space: {json.dumps(str(space_path))}
          num_updates: 1
          pairs_per_update: 1
          num_parallel: 1
          inflight_factor: 1
        """,
    )

    cfg = load_spsa_run_config(config_yaml)
    storage = FilesystemRunStorage(tmp_path)
    session = SessionContext.build(storage=storage, num_workers=2, run_id="test")
    hooks = _CapturingHooks()
    orch = SpsaOrchestrator(
        cfg,
        session=session,
        hooks=hooks,
        engine_factory_service=_mock_engine_factory_service,
        ledger_runtime=Mock(game_result_kind=Mock(return_value=None)),
    )
    params = load_spsa_space_spec(space_path).to_param_entries()
    orch.set_work_items([1], params, ["startpos"])

    async def fake_execute_game(_orchestrator, spec):
        return rsshogi.record.Record.from_dict(
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

    import shogiarena._core.contexts.spsa.adapters.orchestrator_gameplay_mixin as _gpm

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(_gpm, "_execute_game_service", fake_execute_game)
    monkeypatch.setattr(
        _gpm,
        "extract_participation",
        lambda _record: (
            GameParticipationRecord(role="black", engine_name="black"),
            GameParticipationRecord(role="white", engine_name="white"),
        ),
    )

    await orch._run_game(
        start_sfen="startpos",
        tuned_params=params,
        current_params=params,
        worker_idx=1,  # intentionally differs from actual slot the orchestrator will pick (0)
        is_tuned_as_black=True,
        update_idx=1,
        phase="plus",
        preassigned_game_id=None,
        pair_id="spsa-u000001-p000000",
    )
    monkeypatch.undo()

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
    space_path = _write_space(write)
    config_yaml = write(
        "cfg/spsa.yaml",
        f"""
        experiment_name: exp
        engines:
          - engine_path: {json.dumps(str(engine_cfg))}
            name: tuned
        rules:
          time_control:
            time_ms: 60000
            byoyomi_ms: 1000
          initial_positions:
            type: file
            source: {json.dumps(str(sfens))}
        spsa:
          space: {json.dumps(str(space_path))}
          num_updates: 1
          pairs_per_update: 1
          num_parallel: 1
          inflight_factor: 1
        """,
    )

    cfg = load_spsa_run_config(config_yaml)
    storage = FilesystemRunStorage(tmp_path)
    session = SessionContext.build(storage=storage, num_workers=2, run_id="test")
    hooks = _CapturingHooks()
    orch = SpsaOrchestrator(
        cfg,
        session=session,
        hooks=hooks,
        engine_factory_service=_mock_engine_factory_service,
        ledger_runtime=Mock(game_result_kind=Mock(return_value=None)),
    )
    params = load_spsa_space_spec(space_path).to_param_entries()
    orch.set_work_items([1], params, ["startpos"])

    # Force preassignment failure so that initial resolved worker is None
    orch.worker_busy = {0, 1}

    async def fake_execute_game(_orchestrator, spec):
        orch.game_to_worker[numeric_game_id(spec.game_id)] = 1
        return rsshogi.record.Record.from_dict(
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

    import shogiarena._core.contexts.spsa.adapters.orchestrator_gameplay_mixin as _gpm

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(_gpm, "_execute_game_service", fake_execute_game)
    monkeypatch.setattr(
        _gpm,
        "extract_participation",
        lambda _record: (
            GameParticipationRecord(role="black", engine_name="black"),
            GameParticipationRecord(role="white", engine_name="white"),
        ),
    )

    await orch._run_game(
        start_sfen="startpos",
        tuned_params=params,
        current_params=params,
        worker_idx=7,
        is_tuned_as_black=True,
        update_idx=1,
        phase="plus",
        preassigned_game_id="test-game",
        pair_id="spsa-u000001-p000000",
    )

    monkeypatch.undo()

    assert hooks.events, "expected SPSA completion event"
    assert hooks.events[-1].worker_idx == 1

    await orch.shutdown()
