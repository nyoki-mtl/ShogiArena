from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from shogiarena._core.contexts.spsa.adapters import derived_json_scheduler as scheduler_module
from shogiarena._core.contexts.spsa.adapters.derived_json_scheduler import SpsaDerivedJsonScheduler
from shogiarena._core.contexts.spsa.adapters.ledger_runtime import SpsaLedgerRuntime
from shogiarena._core.contexts.spsa.adapters.ledger_store import open_spsa_ledger
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry

RUN_ID = "run-1"


def _seed_ledger(run_dir: Path) -> None:
    """Create an on-disk ledger with one committed update."""

    ledger = open_spsa_ledger(run_dir)
    try:
        runtime = SpsaLedgerRuntime(ledger.connection, run_id=RUN_ID)
        runtime.initialize_run(
            resume_hash="resume",
            space_digest="space",
            sealed_run_seed="seed",
            contract={"config": {"num_updates": 2, "int_rounding": "nearest", "crn_enabled": True}},
            params=[
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
            ],
        )
        runtime.plan_update(
            update_idx=1,
            theta_before={"Threads": 1.0},
            schedule={
                "k_pair": 1,
                "pair_index_end": 1,
                "expected_pair_ids": {"SPSA": ["spsa-u000001-p000000"], "LTC": []},
            },
            ltc_required=False,
        )
        runtime.assign_pair(
            update_idx=1,
            pair_id="spsa-u000001-p000000",
            assignment_kind="SPSA",
            opening={"start_sfen": "startpos"},
            color_assignment={
                "games": [
                    {"slot": "black", "tuned_as": "black", "game_id": "spsa-u000001-p000000-black"},
                    {"slot": "white", "tuned_as": "white", "game_id": "spsa-u000001-p000000-white"},
                ]
            },
            flips={"Threads": 1},
            rounding_samples={"Threads": 2},
        )
        runtime.mark_games_running(update_idx=1)
        for game_id, result_kind, game_db_id in (
            ("spsa-u000001-p000000-black", "BLACK_WIN", 1),
            ("spsa-u000001-p000000-white", "DRAW", 2),
        ):
            ledger.connection.execute(
                """
                INSERT INTO game_observations (
                    run_id, game_id, update_idx, pair_id, attempt_id, observation_kind, result_kind,
                    game_db_id, evidence_digest, observed_at
                ) VALUES ('run-1', ?, 1, 'spsa-u000001-p000000', ?, 'SPSA', ?, ?, ?, ?)
                """,
                (
                    game_id,
                    f"attempt-{game_db_id}",
                    result_kind,
                    game_db_id,
                    f"digest-{game_db_id}",
                    f"2026-07-27T00:00:0{game_db_id}+00:00",
                ),
            )
        ledger.connection.commit()
        runtime.mark_games_complete(update_idx=1)
        runtime.store_candidate(
            update_idx=1,
            theta_candidate={"Threads": 2.0},
            schedule={"score_sum": 1.0, "gradients": {"Threads": 0.5}, "deltas": {"Threads": 1.0}},
        )
        runtime.commit_without_ltc(update_idx=1, theta_final={"Threads": 2.0})
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_project_blocking_writes_the_compatibility_views(tmp_path: Path) -> None:
    _seed_ledger(tmp_path)
    scheduler = SpsaDerivedJsonScheduler(run_dir=tmp_path, run_id=RUN_ID)
    try:
        scheduler.project_blocking()
    finally:
        scheduler.close()

    current = json.loads((tmp_path / "spsa" / "current.json").read_text(encoding="utf-8"))
    index = json.loads((tmp_path / "spsa" / "index.json").read_text(encoding="utf-8"))
    assert current["theta"] == {"Threads": 2.0}
    assert index["metadata"]["total_updates"] == 1


@pytest.mark.asyncio
async def test_request_runs_projection_off_the_event_loop(tmp_path: Path) -> None:
    _seed_ledger(tmp_path)
    scheduler = SpsaDerivedJsonScheduler(run_dir=tmp_path, run_id=RUN_ID, min_interval_s=0.0)
    try:
        scheduler.request()
        assert not (tmp_path / "spsa" / "index.json").exists(), "request must not project inline"
        await scheduler.drain()
    finally:
        scheduler.close()

    assert (tmp_path / "spsa" / "index.json").is_file()


@pytest.mark.asyncio
async def test_burst_of_requests_coalesces_into_fewer_projections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_ledger(tmp_path)
    calls: list[str] = []

    def _counting_projection(*, connection: object, run_id: str, run_dir: Path) -> None:
        del connection, run_dir
        calls.append(run_id)

    monkeypatch.setattr(scheduler_module, "project_spsa_ledger", _counting_projection)
    scheduler = SpsaDerivedJsonScheduler(run_dir=tmp_path, run_id=RUN_ID, min_interval_s=0.0)
    try:
        for _ in range(50):
            scheduler.request()
        await scheduler.drain()
    finally:
        scheduler.close()

    # 50 game completions must not translate into 50 whole-run rebuilds.
    assert 1 <= len(calls) <= 2


@pytest.mark.asyncio
async def test_debounce_defers_the_second_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_ledger(tmp_path)
    calls: list[str] = []

    def _counting_projection(*, connection: object, run_id: str, run_dir: Path) -> None:
        del connection, run_dir
        calls.append(run_id)

    monkeypatch.setattr(scheduler_module, "project_spsa_ledger", _counting_projection)
    scheduler = SpsaDerivedJsonScheduler(run_dir=tmp_path, run_id=RUN_ID, min_interval_s=30.0)
    try:
        scheduler.request()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        scheduler.request()
        await asyncio.sleep(0.05)
        assert len(calls) == 1, "the second request must wait for the debounce window"
    finally:
        await scheduler.drain()
        scheduler.close()


@pytest.mark.asyncio
async def test_drain_stops_accepting_further_requests(tmp_path: Path) -> None:
    _seed_ledger(tmp_path)
    scheduler = SpsaDerivedJsonScheduler(run_dir=tmp_path, run_id=RUN_ID, min_interval_s=0.0)
    try:
        await scheduler.drain()
        scheduler.request()
        await asyncio.sleep(0.05)
        assert not (tmp_path / "spsa" / "index.json").exists()
    finally:
        scheduler.close()


@pytest.mark.asyncio
async def test_unset_min_interval_falls_back_to_the_module_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``min_interval_s=None`` は既定値と同じ debounce になること。

    config 側は既定値を持たず None を渡す。既定値の定義箇所を
    ``DEFAULT_MIN_INTERVAL_S`` 一箇所に保つための回帰テスト。
    """

    _seed_ledger(tmp_path)
    calls: list[str] = []

    def _counting_projection(*, connection: object, run_id: str, run_dir: Path) -> None:
        del connection, run_dir
        calls.append(run_id)

    monkeypatch.setattr(scheduler_module, "project_spsa_ledger", _counting_projection)
    monkeypatch.setattr(scheduler_module, "DEFAULT_MIN_INTERVAL_S", 30.0)
    scheduler = SpsaDerivedJsonScheduler(run_dir=tmp_path, run_id=RUN_ID, min_interval_s=None)
    try:
        scheduler.request()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        scheduler.request()
        await asyncio.sleep(0.05)
        assert len(calls) == 1, "the default debounce window must apply when min_interval_s is None"
    finally:
        await scheduler.drain()
        scheduler.close()


@pytest.mark.asyncio
async def test_request_without_running_loop_is_a_no_op(tmp_path: Path) -> None:
    _seed_ledger(tmp_path)
    scheduler = SpsaDerivedJsonScheduler(run_dir=tmp_path, run_id=RUN_ID, min_interval_s=0.0)

    def _request_outside_loop() -> None:
        scheduler.request()

    try:
        await asyncio.to_thread(_request_outside_loop)
        assert not (tmp_path / "spsa" / "index.json").exists()
    finally:
        scheduler.close()
