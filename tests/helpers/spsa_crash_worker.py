from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Literal

import rsshogi
from rsshogi.initial_positions import InitialPosition

import shogiarena._core.contexts.spsa.adapters.runner as runner_module
from shogiarena._core.contexts.spsa.adapters.ledger_observations import SpsaObservationLedger
from shogiarena._core.contexts.spsa.adapters.ledger_runtime import SpsaLedgerRuntime
from shogiarena._core.contexts.spsa.adapters.ledger_store import open_spsa_ledger
from shogiarena._core.contexts.spsa.adapters.runner import SpsaRunner
from shogiarena._core.contexts.spsa.application.runner_state import SpsaRunnerState
from shogiarena._core.contexts.spsa.domain.ledger_models import LedgerPairAssignment
from shogiarena._core.contexts.spsa.domain.participation_identity import (
    SpsaParticipationIdentity,
    attach_spsa_participation_identity,
)
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry
from shogiarena._core.platform.db.store.arena_db_adapter import ArenaDBAdapter
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.participation_records import GameParticipationRecord

_CRASH_EXIT = 87


def _maybe_crash(requested: str | None, point: str) -> None:
    if requested == point:
        os._exit(_CRASH_EXIT)


def _record_authority_games(
    database: ArenaDBAdapter,
    *,
    pair_id: str,
    kind: Literal["SPSA", "LTC"],
) -> None:
    for slot, result in (("black", GameResult.BLACK_WIN), ("white", GameResult.DRAW_BY_REPETITION)):
        game_id = f"{pair_id}-{slot}"
        if database.get_game_id_by_name(game_id) is not None:
            continue
        identity = SpsaParticipationIdentity(
            run_id="run-1",
            update_idx=1,
            pair_id=pair_id,
            attempt_id=f"{pair_id}-attempt-{slot}",
            observation_kind=kind,
        )
        participation = attach_spsa_participation_identity(
            (
                GameParticipationRecord(role="black", engine_name="black"),
                GameParticipationRecord(role="white", engine_name="white"),
            ),
            identity=identity,
        )
        database.append_record_with_participation(
            _make_record(game_name=game_id, result=result),
            participation=participation,
        )


def _assign_pair(runtime: SpsaLedgerRuntime, *, pair_id: str) -> None:
    runtime.assign_pair(
        update_idx=1,
        pair_id=pair_id,
        assignment_kind="LTC" if pair_id.startswith("ltc-") else "SPSA",
        opening={"start_sfen": "startpos"},
        color_assignment={
            "games": [
                {"slot": "black", "tuned_as": "black", "game_id": f"{pair_id}-black"},
                {"slot": "white", "tuned_as": "white", "game_id": f"{pair_id}-white"},
            ]
        },
        flips={"Threads": 1} if pair_id.startswith("spsa-") else {},
        rounding_samples={"Threads": 2} if pair_id.startswith("spsa-") else {},
    )


def _make_record(*, game_name: str, result: GameResult) -> rsshogi.record.Record:
    return rsshogi.record.Record.from_dict(
        {
            "metadata": {
                "game_name": game_name,
                "game_type": "spsa",
                "black_player": "black",
                "white_player": "white",
                "start_date": "2026-07-27T00:00:00+00:00",
                "end_date": "2026-07-27T00:01:00+00:00",
                "updated_date": "2026-07-27T00:01:00+00:00",
                "attributes": {
                    "game_name": game_name,
                    "game_type": "spsa",
                    "updated_date": "2026-07-27T00:01:00+00:00",
                },
            },
            "init_position_sfen": InitialPosition.STANDARD.value,
            "moves": [],
            "result": {"result": result.name, "ply_count": 0},
        }
    )


def _snapshot(runtime: SpsaLedgerRuntime, path: Path) -> None:
    terminal = runtime.terminal_payload()
    if terminal is not None:
        terminal.pop("committed_at", None)
    payload = {
        "completed_updates": runtime.completed_updates(),
        "theta": runtime.current_theta(),
        "accepted_baseline": runtime.accepted_baseline(),
        "update_state": runtime.update_state(1).value,
        "terminal": terminal,
        "next_action": None if terminal is not None else runtime.update_state(1).value,
        "artifacts": {
            name: (path.parent / name).exists()
            for name in (
                "result.json",
                "completion_status.json",
                "completed.flag",
                "spsa/terminal.json",
            )
        },
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


class _ResultStore:
    def __init__(self, run_dir: Path, crash_point: str | None) -> None:
        self._run_dir = run_dir
        self._crash_point = crash_point

    def save_result(self, result: object) -> None:
        write_json_atomic(self._run_dir / "result.json", {"run_id": str(result.run_id)})
        _maybe_crash(self._crash_point, "result_persisted")


class _TerminalRuntime:
    def __init__(self, runtime: SpsaLedgerRuntime, crash_point: str | None) -> None:
        self._runtime = runtime
        self._crash_point = crash_point

    def __getattr__(self, name: str) -> object:
        return getattr(self._runtime, name)

    def commit_terminal(self, *, status: str, reason: str, resumable: bool) -> None:
        _maybe_crash(self._crash_point, "terminal_before_commit")
        self._runtime.commit_terminal(status=status, reason=reason, resumable=resumable)
        _maybe_crash(self._crash_point, "terminal_after_commit")


class _DispatchRuntime:
    def __init__(self, runtime: SpsaLedgerRuntime, crash_point: str | None) -> None:
        self._runtime = runtime
        self._crash_point = crash_point

    def invalidate_resumable_terminal(self) -> None:
        _maybe_crash(self._crash_point, "dispatch_after_artifact_invalidation")
        self._runtime.invalidate_resumable_terminal()


class _DispatchController:
    def __init__(self, run_dir: Path) -> None:
        self._run_dir = run_dir

    async def run_orchestrator(self, _orchestrator: object, run_coro: object) -> None:
        if hasattr(run_coro, "close"):
            run_coro.close()
        (self._run_dir / "dispatch.started").write_text("started", encoding="utf-8")


async def _stop_runner(runner: SpsaRunner) -> None:
    await runner._stop_additional_services()


def _finalize_with_runner(
    *,
    run_dir: Path,
    database: ArenaDBAdapter,
    ledger: object,
    runtime: SpsaLedgerRuntime,
    params: list[ParamEntry],
    crash_point: str | None,
) -> None:
    runner = object.__new__(SpsaRunner)
    runner.run_dir = run_dir
    runner.config = SimpleNamespace(num_updates=1)
    runner._storage = SimpleNamespace(run_dir=run_dir)
    runner._state = SpsaRunnerState(
        db_service=database,
        ledger=ledger,
        ledger_runtime=_TerminalRuntime(runtime, crash_point),  # type: ignore[arg-type]
        params=params,
    )
    runner._result_store = _ResultStore(run_dir, crash_point)
    runner._progress = SimpleNamespace(finalize=lambda _payload: None)
    runner.stop_services = MethodType(_stop_runner, runner)
    original_builder = runner_module.build_spsa_final_result
    original_writer = runner_module.write_json_atomic
    write_count = 0

    def _crashing_writer(path: Path, payload: object) -> None:
        nonlocal write_count
        original_writer(path, payload)
        write_count += 1
        if write_count == 1:
            _maybe_crash(crash_point, "terminal_artifact_published")
        if write_count == 2:
            _maybe_crash(crash_point, "completion_artifact_published")

    runner_module.build_spsa_final_result = lambda **_kwargs: SimpleNamespace(run_id="run-1")
    runner_module.write_json_atomic = _crashing_writer
    try:
        asyncio.run(runner.finalize_and_persist(None))
    finally:
        runner_module.build_spsa_final_result = original_builder
        runner_module.write_json_atomic = original_writer
    _maybe_crash(crash_point, "completed_flag_published")


def run(run_dir: Path, *, crash_point: str | None, decision: str) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    database = ArenaDBAdapter(SQLiteShogiDBFactory(run_dir / "game.db"))
    database.ensure_schema()
    ledger = open_spsa_ledger(run_dir)
    runtime = SpsaLedgerRuntime(ledger.connection, run_id="run-1")
    params = [
        ParamEntry(
            name="Threads",
            type="int",
            value=1.0,
            min=1.0,
            max=8.0,
            step=1.0,
            delta=1.0,
            comment="",
            is_not_used=False,
        )
    ]
    runtime.initialize_run(
        resume_hash="resume",
        space_digest="space",
        sealed_run_seed="seed",
        contract={
            "config": {
                "num_updates": 1,
                "ltc_regression": {
                    "pass_criteria": {
                        "min_winrate": 0.5 if decision == "pass" else 0.8,
                        "max_elo_drop": None,
                        "sprt": None,
                    }
                },
            }
        },
        params=params,
    )

    runtime.plan_update(
        update_idx=1,
        theta_before={"Threads": 1.0},
        schedule={
            "k_pair": 1,
            "pair_index_end": 1,
            "expected_pair_ids": {
                "SPSA": ["spsa-u000001-p000000"],
                "LTC": ["ltc-u000001-p000000"],
            },
        },
        ltc_required=True,
    )
    tuning_pair = "spsa-u000001-p000000"
    _assign_pair(runtime, pair_id=tuning_pair)
    runtime.mark_games_running(update_idx=1)
    _record_authority_games(database, pair_id=tuning_pair, kind="SPSA")
    _maybe_crash(crash_point, "game_db_commit")

    observations = SpsaObservationLedger(ledger.connection)
    observations.reconcile(run_id="run-1", source=database)
    _maybe_crash(crash_point, "ledger_observation_commit")
    runtime.mark_games_complete(update_idx=1)
    _maybe_crash(crash_point, "pairs_complete")
    runtime.store_candidate(
        update_idx=1,
        theta_candidate={"Threads": 2.0},
        schedule={"score_sum": 1.0, "pair_index_end": 1},
    )
    _maybe_crash(crash_point, "candidate_saved")
    runtime.prepare_ltc(update_idx=1)
    _maybe_crash(crash_point, "ltc_pending")

    ltc_pair = "ltc-u000001-p000000"
    runtime.assign_ltc_pairs_and_start(
        update_idx=1,
        assignments=[
            LedgerPairAssignment(
                pair_id=ltc_pair,
                opening={"start_sfen": "startpos"},
                color_assignment={
                    "games": [
                        {"slot": "black", "tuned_as": "black", "game_id": f"{ltc_pair}-black"},
                        {"slot": "white", "tuned_as": "white", "game_id": f"{ltc_pair}-white"},
                    ]
                },
                flips={},
                rounding_samples={},
            )
        ],
    )
    _record_authority_games(database, pair_id=ltc_pair, kind="LTC")
    observations.reconcile(run_id="run-1", source=database)
    _maybe_crash(crash_point, "ltc_result_persisted")

    before_point = "accepted_before_commit" if decision == "pass" else "reverted_before_commit"
    after_point = "accepted_after_commit" if decision == "pass" else "reverted_after_commit"
    _maybe_crash(crash_point, before_point)
    runtime.commit_ltc_decision(
        update_idx=1,
        baseline_update_idx=0,
        is_passed=decision == "pass",
        evidence={
            "update_idx": 1,
            "status": "passed" if decision == "pass" else "failed",
            "is_accepted": decision == "pass",
            "total_pairs": 1,
            "pairs_played": 1,
            "total_games": 2,
            "tuned_wins": 1,
            "baseline_wins": 0,
            "draws": 1,
            "winrate": 0.75,
            "elo": -400 * math.log10(1 / 0.75 - 1),
            "average_score": 0.5,
            "fail_reasons": ([] if decision == "pass" else ["winrate 0.750 below threshold 0.800"]),
            "sprt": None,
            "sprt_decision": None,
        },
        accepted_theta={"Threads": 2.0},
        reverted_theta={"Threads": 1.0},
    )
    _maybe_crash(crash_point, after_point)
    _finalize_with_runner(
        run_dir=run_dir,
        database=database,
        ledger=ledger,
        runtime=runtime,
        params=params,
        crash_point=crash_point,
    )
    with open_spsa_ledger(run_dir, read_only=True) as final_ledger:
        _snapshot(
            SpsaLedgerRuntime(final_ledger.connection, run_id="run-1"),
            run_dir / "snapshot.json",
        )


def run_dispatch_probe(run_dir: Path, *, crash_point: str | None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    ledger = open_spsa_ledger(run_dir)
    runtime = SpsaLedgerRuntime(ledger.connection, run_id="run-1")
    params = [
        ParamEntry(
            name="Threads",
            type="int",
            value=1.0,
            min=1.0,
            max=8.0,
            step=1.0,
            delta=1.0,
            comment="",
            is_not_used=False,
        )
    ]
    runtime.initialize_run(
        resume_hash="resume",
        space_digest="space",
        sealed_run_seed="seed",
        contract={"config": {"num_updates": 1}},
        params=params,
    )
    if runtime.terminal_payload() is None:
        runtime.commit_terminal(
            status="with-anomalies",
            reason="cancelled_resumable",
            resumable=True,
        )
        write_json_atomic(run_dir / "completion_status.json", {"status": "with-anomalies"})
        write_json_atomic(run_dir / "spsa" / "terminal.json", {"reason": "cancelled_resumable"})

    runner = object.__new__(SpsaRunner)
    runner.run_dir = run_dir
    runner._storage = SimpleNamespace(run_dir=run_dir)
    runner._state = SpsaRunnerState(
        ledger=ledger,
        ledger_runtime=_DispatchRuntime(runtime, crash_point),  # type: ignore[arg-type]
        has_resumable_terminal=True,
    )
    runner._run_controller = _DispatchController(run_dir)

    async def _noop() -> None:
        return None

    asyncio.run(runner.run_orchestrator(SimpleNamespace(), _noop()))
    status = ledger.connection.execute("SELECT status FROM run_contract WHERE run_id = 'run-1'").fetchone()
    write_json_atomic(
        run_dir / "dispatch-snapshot.json",
        {
            "run_status": None if status is None else str(status[0]),
            "terminal_present": runtime.terminal_payload() is not None,
            "completion_present": (run_dir / "completion_status.json").exists(),
            "terminal_artifact_present": (run_dir / "spsa" / "terminal.json").exists(),
            "dispatch_started": (run_dir / "dispatch.started").exists(),
        },
    )
    ledger.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--crash-point")
    parser.add_argument("--decision", choices=("pass", "fail"), default="pass")
    parser.add_argument("--dispatch-only", action="store_true")
    args = parser.parse_args()
    if args.dispatch_only:
        run_dispatch_probe(args.run_dir, crash_point=args.crash_point)
    else:
        run(args.run_dir, crash_point=args.crash_point, decision=args.decision)


if __name__ == "__main__":
    main()
