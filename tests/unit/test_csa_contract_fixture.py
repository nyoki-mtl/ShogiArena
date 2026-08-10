"""The consumer half of the CSA event-log contract.

`rsshogi-csa` produces `csa-event-log-contract.jsonl` by driving its real session
core; this reads the same bytes with the real parser, fold and snapshot builder.
Two independent readings of one artifact is the point — the drift this replaced
was two descriptions of a format that each side verified against itself.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from shogiarena._core.contexts.csa_watch.application.live_snapshots import build_summary
from shogiarena._core.contexts.csa_watch.application.run_watcher import RunView, apply_chunk
from shogiarena._core.contexts.csa_watch.domain.run_state import RunState
from shogiarena._core.contexts.csa_watch.ports.log_source_ports import LogChunk

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "csa_contract"
FIXTURE = FIXTURE_DIR / "csa-event-log-contract.jsonl"
PROVENANCE = FIXTURE_DIR / "PROVENANCE.json"


def _lines() -> tuple[str, ...]:
    return tuple(line for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip())


def _fold(lines: tuple[str, ...]) -> RunState:
    state, _ = apply_chunk(RunState(run_id="1800000000"), LogChunk(lines=lines, has_partial_line=False))
    return state


def test_the_vendored_bytes_are_the_ones_the_provenance_names() -> None:
    """A vendored artifact that was edited here is no longer the contract."""
    recorded = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert digest == recorded["sha256"], (
        "the fixture does not match its provenance; it must be re-vendored from "
        f"{recorded['producer_repository']} rather than edited here"
    )
    assert len(recorded["producer_commit"]) == 40


def test_every_record_is_understood() -> None:
    """No line in the canonical run is unknown to this build."""
    state = _fold(_lines())
    assert state.health.unknown_types == {}
    assert state.health.malformed == 0
    assert state.health.invalid_lines == 0
    assert state.health.missing_seq == 0


def test_engine_meta_carries_the_run_static_options() -> None:
    state = _fold(_lines())
    assert state.engine_name == "Fixture Engine"
    assert state.engine_author == "Fixture Author"
    assert state.engine_options == (("Threads", "4"), ("USI_Hash", "1024"))


def test_the_entering_king_rule_is_a_property_of_the_game_not_the_engine() -> None:
    """The two facts are kept apart, because they are settled at different times.

    `engine_meta.options` is what the engine was initialised with, once, for the
    whole run. The entering-king rule is decided per game from the server's
    proposal — here derived from the summary rather than configured — so the game
    is the only thing that can state it correctly for every game.
    """
    state = _fold(_lines())
    assert "EnteringKingRule" not in dict(state.engine_options)

    game = state.games[-1]
    assert game.entering_king_rule == "CSARule27", "derived from the proposal's Declaration line"


def test_liveness_records_travel_in_the_same_fixture() -> None:
    state = _fold(_lines())
    assert state.emits_liveness, "the run wrote a heartbeat"
    assert state.stopped, "the run wrote bridge_stop, so its log is complete"


def test_multi_game_lifecycle_keeps_run_and_game_identity_separate() -> None:
    records = [json.loads(line) for line in _lines()]
    state = _fold(_lines())

    assert len(state.games) == 2
    assert [game.my_color for game in state.games] == ["black", "white"]
    assert state.current_game_id is None
    first_end = next(index for index, record in enumerate(records) if record["type"] == "game_end")
    second_start = max(index for index, record in enumerate(records) if record["type"] == "game_start")
    assert any(
        record["type"] == "state" and record["phase"] == "idle" for record in records[first_end + 1 : second_start]
    )
    assert records[-1]["type"] == "bridge_stop"
    alerts = [record for record in records if record["type"] == "alert"]
    assert any(record["game_id"] is None for record in alerts)
    assert any(record["game_id"] == state.games[-1].game_id for record in alerts)


def test_an_unknown_field_does_not_stop_the_fold() -> None:
    """Additive changes must not break a reader that predates them.

    Injected here rather than baked into the artifact: the fixture has to stay
    exactly what the producer writes, and a field no producer emits would make it
    something else.
    """
    lines = list(_lines())
    for index, line in enumerate(lines):
        record = json.loads(line)
        if record.get("type") == "engine_meta":
            record["a_field_from_the_future"] = {"nested": [1, 2, 3]}
            lines[index] = json.dumps(record)
            break
    else:  # pragma: no cover - the fixture always has one
        pytest.fail("the fixture should contain an engine_meta record")

    state = _fold(tuple(lines))
    assert state.engine_name == "Fixture Engine"
    assert state.health.malformed == 0


def test_the_snapshot_the_page_boots_on_survives_the_whole_run() -> None:
    """Fold is not enough: the published shape is what the dashboard consumes."""
    state = _fold(_lines())
    view = RunView(worker_idx=0, state=state, path=FIXTURE)
    summary = build_summary((view,), timestamp="2026-08-07T00:00:00+00:00", run_dir=str(FIXTURE_DIR))

    runs = summary["csa_runs"]
    assert isinstance(runs, list) and len(runs) == 1
    entry = runs[0]
    assert entry["engine_name"] == "Fixture Engine"
    assert entry["engine_options"] == [
        {"name": "Threads", "value": "4"},
        {"name": "USI_Hash", "value": "1024"},
    ]
    assert entry["stopped"] is True
    assert entry["emits_liveness"] is True
    assert entry["current_game_id"] is None
    assert [game["my_color"] for game in entry["game_entries"]] == ["black", "white"]
    assert {alert["game_id"] for alert in entry["alert_entries"]} == {
        None,
        state.games[0].game_id,
        state.games[-1].game_id,
    }
