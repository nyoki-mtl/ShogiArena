from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from shogiarena._core.contexts.spsa.adapters.ledger_runtime import SpsaLedgerRuntime
from shogiarena._core.contexts.spsa.adapters.ledger_schema import create_canonical_schema
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry


def _runtime() -> tuple[sqlite3.Connection, SpsaLedgerRuntime]:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    create_canonical_schema(connection)
    runtime = SpsaLedgerRuntime(connection, run_id="run-1")
    runtime.initialize_run(
        resume_hash="resume",
        space_digest="space",
        sealed_run_seed="seed",
        contract={
            "config": {
                "num_updates": 2,
                "int_rounding": "nearest",
                "crn_enabled": True,
            }
        },
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
    return connection, runtime


def _commit_update(connection: sqlite3.Connection, runtime: SpsaLedgerRuntime) -> None:
    runtime.plan_update(
        update_idx=1,
        theta_before={"Threads": 1.0},
        schedule={
            "k_pair": 1,
            "pair_index_end": 1,
            "expected_pair_ids": {
                "SPSA": ["spsa-u000001-p000000"],
                "LTC": [],
            },
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
                {
                    "slot": "black",
                    "tuned_as": "black",
                    "game_id": "spsa-u000001-p000000-black",
                },
                {
                    "slot": "white",
                    "tuned_as": "white",
                    "game_id": "spsa-u000001-p000000-white",
                },
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
        connection.execute(
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
    connection.commit()
    runtime.mark_games_complete(update_idx=1)
    runtime.store_candidate(
        update_idx=1,
        theta_candidate={"Threads": 2.0},
        schedule={
            "score_sum": 1.0,
            "gradients": {"Threads": 0.5},
            "deltas": {"Threads": 1.0},
        },
    )
    runtime.commit_without_ltc(update_idx=1, theta_final={"Threads": 2.0})


def test_projection_rebuilds_corrupt_current_index_and_events_from_ledger(tmp_path: Path) -> None:
    connection, runtime = _runtime()
    _commit_update(connection, runtime)
    spsa_dir = tmp_path / "spsa"
    spsa_dir.mkdir()
    for name in ("current.json", "index.json", "events.jsonl"):
        (spsa_dir / name).write_text("corrupt", encoding="utf-8")

    runtime.project_derived_json(run_dir=tmp_path)

    current = json.loads((spsa_dir / "current.json").read_text(encoding="utf-8"))
    index = json.loads((spsa_dir / "index.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (spsa_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert current["projection_source"] == "spsa/ledger.sqlite3"
    assert current["update_idx"] == 1
    assert current["theta"] == {"Threads": 2.0}
    assert index["metadata"]["total_updates"] == 1
    assert index["updates"][0]["score_sum"] == 1.0
    assert {event["event"] for event in events} == {"game_result", "update"}
    assert all(event["projection_source"] == "spsa/ledger.sqlite3" for event in events)
    draw_event = next(event for event in events if event.get("game_result") == "DRAW")
    assert draw_event["winner"] == 2


def test_projection_failure_cannot_rollback_committed_ledger_update(tmp_path: Path) -> None:
    connection, runtime = _runtime()
    _commit_update(connection, runtime)
    blocking_file = tmp_path / "not-a-directory"
    blocking_file.write_text("blocked", encoding="utf-8")

    with pytest.raises(OSError):
        runtime.project_derived_json(run_dir=blocking_file)

    assert runtime.completed_updates() == 1
    assert runtime.current_theta() == {"Threads": 2.0}
