from __future__ import annotations

import asyncio
import json
import sqlite3
import threading

import pytest
import yaml
from test_spsa_cli_resume_production import _write_fixture

from shogiarena._core.contexts.match.application.runner_finalize_mixin import GameRunnerFinalizeMixin
from shogiarena._core.contexts.spsa.adapters.ledger_observations import SpsaObservationLedger
from shogiarena.cli import main


@pytest.mark.parametrize("with_ltc", [False, True])
def test_cli_six_games_progress_while_observation_commit_is_blocked(tmp_path, monkeypatch, with_ltc):
    config, run_dir, *_ = _write_fixture(tmp_path, legacy_crn=False)
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["spsa"].update(num_updates=1, pairs_per_update=3, num_parallel=6)
    if with_ltc:
        data["spsa"]["ltc_regression"] = {
            "enabled": True,
            "every_n_updates": 1,
            "total_pairs": 1,
            "pass_criteria": {"min_winrate": 0.0},
        }
        data["spsa"]["derived_json_min_interval_s"] = 0
    instance = tmp_path / "local.yaml"
    instance.write_text(yaml.safe_dump({"name": "local", "type": "local", "slots": 12, "max_engines": 12}))
    data["instances"] = [str(instance)]
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    entered, progressed = threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    original_insert = SpsaObservationLedger.insert_committed
    inserts = []

    def insert(self, observation):
        assert threading.get_ident() != main_thread
        if not inserts:
            entered.set()
            assert progressed.wait(10)
        result = original_insert(self, observation)
        inserts.append(observation.game_id)
        return result

    original_move = GameRunnerFinalizeMixin._process_move_result
    move_count = 0

    async def move(self, *args, **kwargs):
        nonlocal move_count
        move_count += 1
        if move_count == 2:
            assert await asyncio.to_thread(entered.wait, 10)
        result = await original_move(self, *args, **kwargs)
        if move_count >= 2:
            progressed.set()
        return result

    monkeypatch.setattr(SpsaObservationLedger, "insert_committed", insert)
    monkeypatch.setattr(GameRunnerFinalizeMixin, "_process_move_result", move)
    main(
        ["--output-dir", str(tmp_path / "output"), "run", "spsa", str(config), "--run-dir", str(run_dir), "--no-resume"]
    )
    assert len(inserts) == len(set(inserts)) == (8 if with_ltc else 6)
    if with_ltc:
        index = json.loads((run_dir / "spsa/index.json").read_text())
        assert index["metadata"]["ltc_regression"]["status"] == "passed"
        assert index["metadata"]["ltc_regression"]["last_update_idx"] == 1
    assert json.loads((run_dir / "spsa/terminal.json").read_text())["reason"] == "completed"
    for path in [run_dir / "game.db", run_dir / "spsa/ledger.sqlite3"]:
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
