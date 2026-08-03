from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import rsshogi
from rsshogi.initial_positions import InitialPosition

from shogiarena._core.contexts.game_session.adapters.run_storage import FilesystemRunStorage
from shogiarena._core.contexts.game_session.ports.session_context import SessionContext
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.spsa.adapters.orchestrator import SpsaOrchestrator
from shogiarena._core.contexts.spsa.application.space_spec import load_spsa_space_spec
from shogiarena._core.contexts.spsa.domain.observation import (
    SpsaIncompleteObservationError,
    SpsaObservationError,
    winner_code_from_result,
)
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.participation_records import GameParticipationRecord
from shogiarena._core.shared.kernel.session_hooks import GameCompletionEvent, NoopGameLifecycleHooks
from tests.unit.spsa_config_test_helpers import load_spsa_run_config


@pytest.mark.parametrize(
    ("result", "is_tuned_as_black", "expected"),
    [
        (GameResult.BLACK_WIN, True, 1),
        (GameResult.BLACK_WIN, False, 0),
        (GameResult.WHITE_WIN, True, 0),
        (GameResult.WHITE_WIN, False, 1),
        (GameResult.DRAW_BY_REPETITION, True, 2),
    ],
)
def test_winner_code_accepts_only_valid_wdl(
    result: GameResult,
    is_tuned_as_black: bool,
    expected: int,
) -> None:
    assert winner_code_from_result(result, is_tuned_as_black=is_tuned_as_black) == expected


@pytest.mark.parametrize("result", [GameResult.ERROR, GameResult.INVALID])
def test_winner_code_rejects_failed_observation(result: GameResult) -> None:
    with pytest.raises(SpsaObservationError, match=result.name):
        winner_code_from_result(result, is_tuned_as_black=True)


def test_winner_code_classifies_paused_as_incomplete() -> None:
    with pytest.raises(SpsaIncompleteObservationError, match="PAUSED"):
        winner_code_from_result(GameResult.PAUSED, is_tuned_as_black=True)


def test_winner_code_classifies_missing_result_as_incomplete() -> None:
    with pytest.raises(SpsaIncompleteObservationError, match="missing"):
        winner_code_from_result(None, is_tuned_as_black=True)


class _CapturingHooks(NoopGameLifecycleHooks):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[GameCompletionEvent] = []

    async def on_game_complete(self, event: GameCompletionEvent) -> None:
        self.events.append(event)


def _write_gameplay_fixture(tmp_path: Path) -> tuple[Path, Path]:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    engine_config = config_dir / "engine.yaml"
    engine_config.write_text(
        f"engine_path: {json.dumps(sys.executable)}\noptions: {{}}\n",
        encoding="utf-8",
    )
    sfens = config_dir / "positions.sfen"
    sfens.write_text("startpos\n", encoding="utf-8")
    space = config_dir / "space.yaml"
    space.write_text(
        textwrap.dedent(
            """
            schema_version: shogiarena.spsa.space.v1
            target:
              engine_family: test
              protocol: usi_options
              required_options_policy: strict
              tunable_manifest:
                required: false
                command: usi_tunables
            parameters:
              - id: ParamA
                target:
                  option: ParamA
                  value_encoding: decimal
                value_type: float
                initial: 1.0
                bounds:
                  min: 0.0
                  max: 2.0
                schedule:
                  c_end: 0.1
                  r_end: 0.1
            """
        ).lstrip(),
        encoding="utf-8",
    )
    config = config_dir / "spsa.yaml"
    config.write_text(
        textwrap.dedent(
            f"""
            engines:
              - engine_path: {json.dumps(str(engine_config))}
                name: tuned
            rules:
              time_control:
                time_ms: 1000
                byoyomi_ms: 100
              initial_positions:
                type: file
                source: {json.dumps(str(sfens))}
            spsa:
              space: {json.dumps(str(space))}
              num_updates: 1
              pairs_per_update: 1
              inflight_factor: 1
              num_parallel: 1
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return config, space


@pytest.mark.asyncio
async def test_production_game_pair_rejects_valid_invalid_pair_before_update_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, space_path = _write_gameplay_fixture(tmp_path)
    config = load_spsa_run_config(config_path)
    storage = FilesystemRunStorage(tmp_path)
    session = SessionContext.build(storage=storage, num_workers=1, run_id="integrity")
    hooks = _CapturingHooks()
    orchestrator = SpsaOrchestrator(
        config,
        session=session,
        hooks=hooks,
        engine_factory_service=EngineFactoryService(factory=AsyncMock()),
        ledger_runtime=Mock(game_result_kind=Mock(return_value=None)),
    )
    params = load_spsa_space_spec(space_path).to_param_entries()
    orchestrator.set_work_items([1], params, ["startpos"])
    outcomes = iter([GameResult.BLACK_WIN, GameResult.INVALID, GameResult.INVALID])

    async def fake_execute_game(_orchestrator: object, spec: Any) -> rsshogi.record.Record:
        result = next(outcomes)
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
                "result": {"result": result.name, "ply_count": 0},
            }
        )

    import shogiarena._core.contexts.spsa.adapters.orchestrator_gameplay_mixin as gameplay_module

    monkeypatch.setattr(gameplay_module, "_execute_game_service", fake_execute_game)
    monkeypatch.setattr(
        gameplay_module,
        "extract_participation",
        lambda _record: (
            GameParticipationRecord(role="black", engine_name="black"),
            GameParticipationRecord(role="white", engine_name="white"),
        ),
    )
    try:
        with pytest.raises(SpsaObservationError, match="INVALID"):
            await orchestrator._run_game_pair(
                "startpos",
                params,
                params,
                0,
                update_idx=1,
                phase="plus",
                pair_id="spsa-u000001-p000000",
            )
    finally:
        await orchestrator.shutdown()

    assert len(hooks.events) == 3
    orchestrator._ledger_runtime.record_variant_quarantine.assert_called_once_with(
        update_idx=1,
        pair_id="spsa-u000001-p000000",
        variant_id="v000001+",
        failure_classification="SPSA observation failed: INVALID",
    )
    events_path = tmp_path / "spsa" / "events.jsonl"
    events = (
        [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if events_path.exists()
        else []
    )
    assert all(event.get("event") != "update" for event in events)
    assert sum(event.get("status") == "pending" for event in events) == 1


@pytest.mark.asyncio
async def test_production_game_pair_does_not_retry_failed_observation_after_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, space_path = _write_gameplay_fixture(tmp_path)
    config = load_spsa_run_config(config_path)
    storage = FilesystemRunStorage(tmp_path)
    session = SessionContext.build(storage=storage, num_workers=1, run_id="cancelled-integrity")
    hooks = _CapturingHooks()
    ledger_runtime = Mock(game_result_kind=Mock(return_value=None))
    orchestrator = SpsaOrchestrator(
        config,
        session=session,
        hooks=hooks,
        engine_factory_service=EngineFactoryService(factory=AsyncMock()),
        ledger_runtime=ledger_runtime,
    )
    params = load_spsa_space_spec(space_path).to_param_entries()
    orchestrator.set_work_items([1], params, ["startpos"])

    async def fake_execute_game(_orchestrator: object, spec: Any) -> rsshogi.record.Record:
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
                "result": {"result": GameResult.INVALID.name, "ply_count": 0},
            }
        )

    import shogiarena._core.contexts.spsa.adapters.orchestrator_gameplay_mixin as gameplay_module

    monkeypatch.setattr(gameplay_module, "_execute_game_service", fake_execute_game)
    monkeypatch.setattr(
        gameplay_module,
        "extract_participation",
        lambda _record: (
            GameParticipationRecord(role="black", engine_name="black"),
            GameParticipationRecord(role="white", engine_name="white"),
        ),
    )
    orchestrator.request_stop()
    try:
        with pytest.raises(SpsaIncompleteObservationError, match="stop request"):
            await orchestrator._run_game_pair(
                "startpos",
                params,
                params,
                0,
                update_idx=1,
                phase="plus",
                pair_id="spsa-u000001-p000000",
            )
    finally:
        await orchestrator.shutdown()

    assert len(hooks.events) == 1
    assert hooks.events[0].is_stop_requested is True
    ledger_runtime.record_variant_quarantine.assert_not_called()
    events_path = tmp_path / "spsa" / "events.jsonl"
    events = (
        [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if events_path.exists()
        else []
    )
    assert all(not str(event.get("game_id", "")).endswith("-retry1") for event in events)


@pytest.mark.asyncio
async def test_game_pair_retries_only_failed_side_once() -> None:
    records = [
        rsshogi.record.Record.from_dict(
            {
                "metadata": {"game_name": f"game-{idx}", "black_player": "b", "white_player": "w"},
                "init_position_sfen": InitialPosition.STANDARD.value,
                "moves": [],
                "result": {"result": result.name, "ply_count": 0},
            }
        )
        for idx, result in enumerate(
            (GameResult.BLACK_WIN, GameResult.INVALID, GameResult.DRAW_BY_REPETITION),
            start=1,
        )
    ]

    class _Harness:
        _run_game_pair = SpsaOrchestrator._run_game_pair

        def __init__(self) -> None:
            self.calls: list[str] = []
            self.reservations: list[str] = []
            self._ledger_runtime = Mock()
            self._stop_event = asyncio.Event()

        async def _run_game(self, **kwargs: Any) -> tuple[int, rsshogi.record.Record]:
            game_id = str(kwargs["preassigned_game_id"])
            self.calls.append(game_id)
            record = records.pop(0)
            return winner_code_from_result(
                record.result,
                is_tuned_as_black=bool(kwargs["is_tuned_as_black"]),
            ), record

        def _reserve_pending_game(self, **kwargs: Any) -> str:
            game_id = str(kwargs["game_id"])
            self.reservations.append(game_id)
            return game_id

    harness = _Harness()
    score, _black, _white = await harness._run_game_pair(
        "startpos",
        [],
        [],
        0,
        update_idx=1,
        phase="plus",
        reserved_ids=("pair-black", "pair-white"),
        pair_id="spsa-u000001-p000000",
    )

    assert score == 0.5
    assert harness.calls == ["pair-black", "pair-white", "pair-white-retry1"]
    assert harness.reservations == ["pair-white-retry1"]
    harness._ledger_runtime.record_variant_quarantine.assert_not_called()
