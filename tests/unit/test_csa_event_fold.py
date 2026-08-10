"""CSA イベントログの畳み込み（0067）。

fixture は `tools/generate_csa_event_fixtures.py` が生成した合成データである。
実ログは相手エンジン名と絶対パスを含むため commit しない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shogiarena._core.contexts.csa_watch.application.run_watcher import apply_chunk
from shogiarena._core.contexts.csa_watch.domain.event_records import (
    InvalidLine,
    KnownRecord,
    MalformedRecord,
    MovePayload,
    UnknownRecord,
    parse_event_record,
)
from shogiarena._core.contexts.csa_watch.domain.fold import fold, fold_all
from shogiarena._core.contexts.csa_watch.domain.run_state import RunState, side_to_move_of
from shogiarena._core.contexts.csa_watch.ports.log_source_ports import LogChunk

FIXTURE_DIR = Path("tests/fixtures/csa_watch")

EVEN_COMPLETE = "1800000000"
PONDER_OUTCOMES = "1800001000"
ENGINE_DEATH = "1800002000"
FUTURE_RECORDS = "1800003000"
SEQ_GAP = "1800004000"
BUOY_HANDICAP = "1800005000"
ILLEGAL_MOVE = "1800006000"
TRUNCATED = "1800007000"


def _fixture_text(run_id: str) -> str:
    return (FIXTURE_DIR / f"{run_id}-events.jsonl").read_text(encoding="utf-8")


def fold_fixture(run_id: str) -> RunState:
    text = _fixture_text(run_id)
    lines = tuple(line for line in text.split("\n") if line.strip())
    # A file that does not end in a newline has an unfinished record at the tail.
    if not text.endswith("\n"):
        lines = lines[:-1]
    state, _ = apply_chunk(RunState(run_id=run_id), LogChunk(lines=lines, has_partial_line=not text.endswith("\n")))
    return state


def _envelope(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"seq": 1, "at": 0, "ts": 1_800_000_000_000, "type": "state", "phase": "warming"}
    payload.update(overrides)
    return payload


class TestEnvelopeParsing:
    def test_missing_seq_is_not_a_record(self) -> None:
        payload = _envelope()
        del payload["seq"]
        assert isinstance(parse_event_record(payload), InvalidLine)

    def test_missing_at_is_not_a_record(self) -> None:
        payload = _envelope()
        del payload["at"]
        assert isinstance(parse_event_record(payload), InvalidLine)

    def test_missing_type_is_not_a_record(self) -> None:
        payload = _envelope()
        del payload["type"]
        assert isinstance(parse_event_record(payload), InvalidLine)

    def test_missing_ts_and_schema_stay_readable(self) -> None:
        payload = _envelope()
        del payload["ts"]
        payload.pop("schema", None)
        parsed = parse_event_record(payload)
        assert isinstance(parsed, KnownRecord)
        assert parsed.envelope.ts is None

    def test_unknown_type_is_kept_not_rejected(self) -> None:
        parsed = parse_event_record(_envelope(type="weather", outlook="unsettled"))
        assert isinstance(parsed, UnknownRecord)
        assert parsed.envelope.type == "weather"

    def test_known_type_with_broken_payload_is_malformed(self) -> None:
        parsed = parse_event_record(_envelope(type="move", ply="not-an-int"))
        assert isinstance(parsed, MalformedRecord)

    def test_unknown_body_fields_are_ignored(self) -> None:
        parsed = parse_event_record(_envelope(type="state", phase="playing", operator_note="added later"))
        assert isinstance(parsed, KnownRecord)

    def test_unknown_move_origin_does_not_break_parsing(self) -> None:
        parsed = parse_event_record(_envelope(type="move", ply=1, side="black", usi="7g7f", by="fallback_new_strategy"))
        assert isinstance(parsed, KnownRecord)
        payload = parsed.payload
        assert isinstance(payload, MovePayload)
        assert payload.is_fallback


class TestWallClockMapping:
    def test_deadline_is_mapped_through_the_record_that_carries_it(self) -> None:
        parsed = parse_event_record(
            _envelope(type="go", seq=2, at=1_000, ts=1_800_000_010_000, ply=4, deadline_at=6_000)
        )
        assert isinstance(parsed, KnownRecord)
        # ts - at = 1_799_999_009_000; deadline 6_000 maps to 1_800_000_015_000.
        assert parsed.envelope.wall_clock_for(6_000) == 1_800_000_015_000

    def test_no_ts_means_no_wall_clock(self) -> None:
        parsed = parse_event_record(_envelope(type="go", ply=1, deadline_at=5, ts=None))
        assert isinstance(parsed, KnownRecord)
        assert parsed.envelope.wall_clock_for(5) is None


class TestFoldedFixtures:
    def test_even_game_folds_to_a_finished_win(self) -> None:
        state = fold_fixture(EVEN_COMPLETE)
        assert len(state.games) == 1
        game = state.games[0]
        assert game.is_finished
        assert game.result == "win"
        assert game.current_ply == 24
        assert state.score.wins == 1
        assert state.health.is_clean

    def test_ponder_outcomes_are_tallied(self) -> None:
        state = fold_fixture(PONDER_OUTCOMES)
        assert state.ponder.started == 8
        assert state.ponder.hits + state.ponder.misses == 8
        assert state.ponder.unresolved == 0
        assert state.ponder.hit_rate == pytest.approx(state.ponder.hits / 8)

    def test_engine_death_keeps_the_fallback_move_visible(self) -> None:
        state = fold_fixture(ENGINE_DEATH)
        game = state.games[0]
        assert game.fallback_plies == (9,)
        codes = [alert.code for alert in state.sorted_alerts()]
        assert codes[0] == "engine_dead"
        assert "engine_restarted" in codes

    def test_future_records_do_not_stop_the_fold(self) -> None:
        state = fold_fixture(FUTURE_RECORDS)
        assert state.health.unknown_types == {"weather": 2}
        assert state.health.malformed == 0
        assert len(state.games) == 1
        assert state.games[0].is_finished
        assert state.phase == "closing"

    def test_seq_gaps_are_counted_and_stepped_over(self) -> None:
        state = fold_fixture(SEQ_GAP)
        assert state.health.missing_seq == 3
        assert state.games[0].current_ply == 8

    def test_buoy_start_carries_the_moves_already_played(self) -> None:
        state = fold_fixture(BUOY_HANDICAP)
        game = state.games[0]
        assert game.initial_sfen.split()[1] == "w"
        assert side_to_move_of(game.initial_sfen) == "white"
        assert game.current_ply == 10
        assert game.moves[0].by == "replayed"
        assert game.moves[0].side == "white"
        assert game.moves[1].side == "black"

    def test_truncated_tail_still_reads_everything_before_it(self) -> None:
        complete = fold_fixture(EVEN_COMPLETE)
        truncated = fold_fixture(TRUNCATED)
        assert truncated.games[0].current_ply == complete.games[0].current_ply
        assert truncated.games[0].result == "win"
        # The last complete record before truncation left the run in `playing`.
        assert truncated.phase == "playing"
        assert truncated.health.invalid_lines == 0


class TestFoldMechanics:
    def test_fold_of_an_unreadable_line_is_counted_not_raised(self) -> None:
        state = fold(RunState(run_id="r"), InvalidLine(reason="broken"))
        assert state.health.invalid_lines == 1

    def test_pending_search_clears_when_the_move_lands(self) -> None:
        records = [
            parse_event_record(
                {
                    "seq": 1,
                    "at": 0,
                    "ts": 1_000,
                    "type": "game_start",
                    "game_id": "g",
                    "names": {"black": "a", "white": "b"},
                    "my_color": "black",
                    "initial_sfen": "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1",
                    "moves_so_far": [],
                    "time": {},
                }
            ),
            parse_event_record(
                {"seq": 2, "at": 0, "ts": 1_000, "type": "go", "game_id": "g", "ply": 0, "deadline_at": 30_000}
            ),
        ]
        state = fold_all(RunState(run_id="r"), records)
        game = state.current_game
        assert game is not None
        assert game.pending is not None
        assert game.pending.deadline_ts == 31_000

        state = fold_all(
            state,
            [
                parse_event_record(
                    {
                        "seq": 3,
                        "at": 5_000,
                        "ts": 6_000,
                        "type": "move",
                        "game_id": "g",
                        "ply": 1,
                        "side": "black",
                        "usi": "7g7f",
                        "by": "engine",
                    }
                )
            ],
        )
        game = state.current_game
        assert game is not None
        assert game.pending is None

    def test_a_ponder_for_an_unseen_game_does_not_enter_the_tally(self) -> None:
        record = parse_event_record(
            {"seq": 1, "at": 0, "ts": 1, "type": "ponder", "game_id": "g", "outcome": "hit", "ply": 4}
        )
        state = fold(RunState(run_id="r"), record)
        assert state.ponder.hits == 0
        assert state.health.orphan_records == 1

    def test_a_missing_move_record_is_reported_as_a_ply_gap(self) -> None:
        """Per-ply arrays index by position; a hole would shift every later value."""
        start = parse_event_record(
            {
                "seq": 1,
                "at": 0,
                "ts": 1,
                "type": "game_start",
                "game_id": "g",
                "names": {"black": "a", "white": "b"},
                "my_color": "black",
                "initial_sfen": "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1",
                "moves_so_far": [],
                "time": {},
            }
        )
        # ply 1 never arrives; ply 2 does.
        skipped = parse_event_record(
            {
                "seq": 3,
                "at": 0,
                "ts": 1,
                "type": "move",
                "game_id": "g",
                "ply": 2,
                "side": "white",
                "usi": "3c3d",
                "by": "opponent",
            }
        )
        state = fold_all(RunState(run_id="r"), [start, skipped])
        assert state.health.ply_gaps == 1
        assert not state.health.is_clean

    def test_records_without_a_game_are_counted_as_orphans(self) -> None:
        record = parse_event_record(
            {"seq": 1, "at": 0, "ts": 1, "type": "move", "ply": 1, "side": "black", "usi": "7g7f", "by": "engine"}
        )
        state = fold(RunState(run_id="r"), record)
        assert state.health.orphan_records == 1


def test_every_fixture_line_is_a_readable_json_object() -> None:
    for path in sorted(FIXTURE_DIR.glob("*-events.jsonl")):
        text = path.read_text(encoding="utf-8")
        lines = [line for line in text.split("\n") if line.strip()]
        if not text.endswith("\n"):
            # The truncated fixture ends mid-record on purpose.
            with pytest.raises(json.JSONDecodeError):
                json.loads(lines[-1])
            lines = lines[:-1]
        for line in lines:
            assert isinstance(json.loads(line), dict)


def test_committed_fixtures_match_the_generator() -> None:
    """The fixtures are generated output; a stale one silently weakens every test."""
    from tools.generate_csa_event_fixtures import iter_fixtures

    for name, text in iter_fixtures():
        committed = (FIXTURE_DIR / name).read_text(encoding="utf-8")
        assert committed == text, f"{name} is stale; re-run tools/generate_csa_event_fixtures.py"


class TestLivenessRecords:
    """Liveness records are recognised before the bridge writes them.

    Order matters: if the bridge shipped first, every healthy run would report
    "unknown type heartbeat xN" as log damage — a permanently ringing alarm.
    """

    def _fold_lines(self, lines: list[str]) -> RunState:
        state = RunState(run_id="r")
        for line in lines:
            state = fold(state, parse_event_record(json.loads(line)))
        return state

    def test_a_heartbeat_is_not_damage(self) -> None:
        state = self._fold_lines(
            [
                '{"at":0,"game_id":null,"schema":1,"seq":1,"ts":1000,"type":"heartbeat"}',
            ]
        )
        assert state.health.unknown_types == {}
        assert state.health.malformed == 0
        assert state.emits_liveness is True
        assert state.stopped is False

    def test_a_heartbeat_moves_the_clock_forward(self) -> None:
        """This is the whole point: silence is what marks a dead bridge."""
        state = self._fold_lines(
            [
                '{"at":0,"game_id":null,"phase":"waiting_pairing","schema":1,"seq":1,"ts":1000,"type":"state"}',
                '{"at":60000,"game_id":null,"schema":1,"seq":2,"ts":61000,"type":"heartbeat"}',
            ]
        )
        assert state.last_ts == 61000

    def test_bridge_stop_marks_the_log_complete(self) -> None:
        state = self._fold_lines(
            [
                '{"at":0,"game_id":null,"schema":1,"seq":1,"ts":1000,"type":"bridge_stop"}',
            ]
        )
        assert state.stopped is True
        assert state.emits_liveness is True

    def test_a_run_from_an_older_bridge_claims_no_liveness(self) -> None:
        """Absence of the records is not a fault; it means we may not judge."""
        state = self._fold_lines(
            [
                '{"at":0,"game_id":null,"phase":"playing","schema":1,"seq":1,"ts":1000,"type":"state"}',
            ]
        )
        assert state.emits_liveness is False
        assert state.stopped is False
