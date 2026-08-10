"""`.csa` 書き出しと `game.db` 永続化（0070）。

`.csa` は棋譜が欲しくて作るのではない。全手を実盤に再生し直すことがログと実装の
相互監査であり、それがこの経路の主目的である。
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from rsshogi.record import Record

from shogiarena._core.contexts.csa_watch.adapters.record_sinks import CsaRecordFileWriter, SqliteCsaRecordStore
from shogiarena._core.contexts.csa_watch.adapters.rsshogi_replay import RsshogiBoardReplay
from shogiarena._core.contexts.csa_watch.application.game_export import (
    MATE_SCORE,
    ExportedGame,
    ExportFailure,
    attach_csa_attributes,
    export_game,
    export_games,
    mate_to_centipawns,
    render_csa,
    split_game_id,
)
from shogiarena._core.contexts.csa_watch.application.game_persistence import CsaGamePersister
from shogiarena._core.contexts.csa_watch.application.run_watcher import RunView
from shogiarena._core.contexts.csa_watch.domain.game_identity import csa_game_key
from shogiarena._core.contexts.csa_watch.domain.run_state import RunState
from shogiarena._core.contexts.csa_watch.ports.record_sink_ports import CsaRecordStoreRetryableError
from shogiarena._core.contexts.dashboard.adapters.game_repository import build_games_list_raw_payload
from shogiarena._core.contexts.dashboard.application.game.detail_builder import build_game_detail_payload

from .test_csa_event_fold import (
    BUOY_HANDICAP,
    ENGINE_DEATH,
    EVEN_COMPLETE,
    ILLEGAL_MOVE,
    PONDER_OUTCOMES,
    fold_fixture,
)

REPLAYER = RsshogiBoardReplay()


def _export(run_id: str, *, should_include_comments: bool = True) -> ExportedGame | ExportFailure:
    game = fold_fixture(run_id).games[-1]
    return export_game(game, REPLAYER, should_include_comments=should_include_comments)


def _exported(run_id: str, *, should_include_comments: bool = True) -> ExportedGame:
    outcome = _export(run_id, should_include_comments=should_include_comments)
    assert isinstance(outcome, ExportedGame), outcome
    return outcome


class TestMateScoreMapping:
    @pytest.mark.parametrize(
        ("mate", "expected"),
        [
            (1, MATE_SCORE - 1),
            (-2, -(MATE_SCORE - 2)),
            (0, MATE_SCORE),
            (MATE_SCORE + 100, 0),
            (-(MATE_SCORE + 100), 0),
        ],
    )
    def test_mate_folds_onto_the_centipawn_scale(self, mate: int, expected: int) -> None:
        assert mate_to_centipawns(mate) == expected

    def test_a_deeper_mate_is_never_stronger_than_a_shallower_one(self) -> None:
        assert mate_to_centipawns(1) > mate_to_centipawns(5)


class TestGameIdParsing:
    def test_a_floodgate_game_id_yields_site_event_and_start(self) -> None:
        site, event, start = split_game_id("wdoor+floodgate-300-10F+alice+bob+20260804153004")
        assert site == "wdoor"
        assert event == "floodgate-300-10F"
        assert start == "2026/08/04 15:30:04"

    def test_an_unexpected_shape_yields_nothing_rather_than_a_guess(self) -> None:
        assert split_game_id("not-a-floodgate-id") == (None, None, None)


class TestExportedText:
    def test_the_declaration_and_version_are_v3(self) -> None:
        text = render_csa(_exported(EVEN_COMPLETE).record)
        assert text.startswith("'CSA encoding=UTF-8\nV3.0\n")

    def test_the_output_reads_back(self) -> None:
        text = render_csa(_exported(EVEN_COMPLETE).record)
        restored = Record.from_csa_str(text)
        assert restored.move_count == fold_fixture(EVEN_COMPLETE).games[-1].current_ply

    def test_evaluations_are_written_in_the_floodgate_comment_form(self) -> None:
        text = render_csa(_exported(EVEN_COMPLETE).record)
        eval_lines = [line for line in text.splitlines() if line.startswith("'**")]
        assert eval_lines
        for line in eval_lines:
            assert line.split()[1].lstrip("-").isdigit()
        assert any("#" in line for line in eval_lines)

    def test_the_principal_variation_is_written_in_csa(self) -> None:
        text = render_csa(_exported(EVEN_COMPLETE).record)
        eval_line = next(line for line in text.splitlines() if line.startswith("'**") and len(line.split()) > 3)
        pv_tokens = [token for token in eval_line.split()[2:] if not token.startswith("#")]
        assert pv_tokens
        for token in pv_tokens:
            # Exactly one sign: `Move32.to_csa` already carries the mover's.
            assert token[0] in "+-"
            assert token[1] not in "+-"
            assert len(token) == 7, token

    def test_each_evaluation_is_written_once(self) -> None:
        """`engine_info` renders its own comment line; setting both would duplicate it."""
        text = render_csa(_exported(EVEN_COMPLETE).record)
        lines = text.splitlines()
        for index, line in enumerate(lines[:-1]):
            if line.startswith("'**"):
                assert not lines[index + 1].startswith("'**"), lines[index : index + 2]

    def test_moves_the_opponent_played_carry_no_comment(self) -> None:
        game = fold_fixture(EVEN_COMPLETE).games[-1]
        record = _exported(EVEN_COMPLETE).record
        for move, entry in zip(game.moves, record.moves, strict=True):
            if move.eval is None:
                assert entry.comment is None

    def test_comments_can_be_left_out(self) -> None:
        text = render_csa(_exported(EVEN_COMPLETE, should_include_comments=False).record)
        assert "'**" not in text

    def test_the_consumed_time_of_each_move_is_written(self) -> None:
        text = render_csa(_exported(EVEN_COMPLETE).record)
        assert any(line.startswith("T") and line[1:].isdigit() for line in text.splitlines())

    def test_the_terminal_marker_passes_through_as_written(self) -> None:
        text = render_csa(_exported(EVEN_COMPLETE).record)
        assert "%TORYO" in text

    def test_the_two_header_dates_are_on_the_same_clock(self) -> None:
        """`$START_TIME` comes from the game id (server-local); `$END_TIME` from epoch ms.

        Formatting one in UTC and the other verbatim put the end of the game
        hours before its start.
        """
        text = render_csa(_exported(EVEN_COMPLETE).record)
        start = next(line for line in text.splitlines() if line.startswith("$START_TIME:"))
        end = next(line for line in text.splitlines() if line.startswith("$END_TIME:"))
        started = datetime.strptime(start.removeprefix("$START_TIME:"), "%Y/%m/%d %H:%M:%S")
        ended = datetime.strptime(end.removeprefix("$END_TIME:"), "%Y/%m/%d %H:%M:%S")
        assert ended >= started
        assert ended - started < timedelta(hours=2)

    def test_the_file_is_named_after_the_game(self) -> None:
        exported = _exported(EVEN_COMPLETE)
        assert exported.file_name == f"{exported.game_id}.csa"


class TestNonStandardStarts:
    def test_a_handicap_start_is_exported_and_reads_back(self) -> None:
        exported = _exported(BUOY_HANDICAP)
        text = render_csa(exported.record)
        restored = Record.from_csa_str(text)
        assert restored.init_position_sfen == fold_fixture(BUOY_HANDICAP).games[-1].initial_sfen

    def test_moves_already_played_at_handover_are_part_of_the_record(self) -> None:
        game = fold_fixture(BUOY_HANDICAP).games[-1]
        assert any(move.by == "replayed" for move in game.moves)
        assert _exported(BUOY_HANDICAP).record.move_count == game.current_ply


class TestRefusalToWriteUnverifiedGames:
    def test_an_illegal_move_produces_a_failure_not_a_file(self) -> None:
        outcome = _export(ILLEGAL_MOVE)
        assert isinstance(outcome, ExportFailure)
        assert outcome.ply == 5
        assert outcome.reason

    def test_a_batch_separates_what_can_be_written_from_what_cannot(self) -> None:
        games = [fold_fixture(EVEN_COMPLETE).games[-1], fold_fixture(ILLEGAL_MOVE).games[-1]]
        exported, failures = export_games(games, REPLAYER)
        assert len(exported) == 1
        assert len(failures) == 1


class TestFileWriter:
    def test_writing_produces_readable_bytes(self, tmp_path: Path) -> None:
        exported = _exported(EVEN_COMPLETE)
        target = tmp_path / exported.file_name
        size = CsaRecordFileWriter().write(exported.record, target)
        assert size == target.stat().st_size
        restored = Record.from_csa_str(target.read_text(encoding="utf-8"))
        assert restored.move_count == exported.record.move_count

    def test_the_declared_encoding_matches_the_bytes_on_disk(self, tmp_path: Path) -> None:
        exported = _exported(EVEN_COMPLETE)
        target = tmp_path / exported.file_name
        CsaRecordFileWriter().write(exported.record, target)
        raw = target.read_bytes()
        assert raw.startswith(b"'CSA encoding=UTF-8")
        assert b"\r\n" not in raw


class TestCsaAttributes:
    def test_csa_only_facts_travel_as_metadata_attributes(self) -> None:
        state = fold_fixture(ENGINE_DEATH)
        game = state.games[-1]
        exported = _exported(ENGINE_DEATH)
        record = attach_csa_attributes(exported.record, state, game)
        attributes = record.metadata.attributes
        assert attributes["csa_run_id"] == state.run_id
        assert attributes["csa_my_color"] == game.my_color
        alerts = json.loads(attributes["csa_alerts"])
        assert any(alert["code"] == "engine_dead" for alert in alerts)
        origins = json.loads(attributes["csa_move_origins"])
        assert origins["9"] == "fallback_pv"

    def test_the_ponder_tally_is_recorded(self) -> None:
        state = fold_fixture(PONDER_OUTCOMES)
        record = attach_csa_attributes(_exported(PONDER_OUTCOMES).record, state, state.games[-1])
        ponder = json.loads(record.metadata.attributes["csa_ponder"])
        assert ponder["started"] == 8


def _gapped(state: RunState) -> RunState:
    """Drop two adjacent moves from the middle, as a lost pair of records would.

    Nothing is renumbered: the survivors keep the plies the log gave them, which
    is exactly what makes the hole detectable and what makes the shortened game
    replay legally.
    """
    game = state.games[-1]
    return replace(state, games=(replace(game, moves=game.moves[:4] + game.moves[6:]),))


class TestPersistence:
    def _persist(self, tmp_path: Path, run_id: str) -> tuple[Path, CsaGamePersister, list[ExportFailure]]:
        db_path = tmp_path / "game.db"
        persister = CsaGamePersister(SqliteCsaRecordStore(db_path), REPLAYER)
        view = RunView(worker_idx=0, state=fold_fixture(run_id), path=Path("x-events.jsonl"))
        failures = persister.persist_finished([view])
        return db_path, persister, failures

    def test_a_finished_game_lands_in_the_database(self, tmp_path: Path) -> None:
        db_path, persister, failures = self._persist(tmp_path, EVEN_COMPLETE)
        try:
            assert failures == []
            assert persister.persisted_count == 1
            assert db_path.exists()
        finally:
            persister.close()

    def test_a_game_is_written_once(self, tmp_path: Path) -> None:
        db_path, persister, _ = self._persist(tmp_path, EVEN_COMPLETE)
        try:
            view = RunView(worker_idx=0, state=fold_fixture(EVEN_COMPLETE), path=Path("x-events.jsonl"))
            persister.persist_finished([view])
            assert persister.persisted_count == 1
        finally:
            persister.close()

    def test_an_unfinished_game_is_not_written(self, tmp_path: Path) -> None:
        db_path = tmp_path / "game.db"
        persister = CsaGamePersister(SqliteCsaRecordStore(db_path), REPLAYER)
        try:
            state = RunState(run_id="r")
            persister.persist_finished([RunView(worker_idx=0, state=state, path=Path("r-events.jsonl"))])
            assert persister.persisted_count == 0
        finally:
            persister.close()

    def test_a_gapped_kifu_is_refused_even_though_it_replays(self, tmp_path: Path) -> None:
        """The worst corruption here is the kind that does not look broken.

        The per-ply arrays compact around a missing move, and two adjacent
        missing plies leave side-to-move parity intact — so the shortened game
        replays legally and would land in `game.db` as an ordinary record. This
        database exists to measure engines; fabricated data in it is worse than
        missing data.
        """
        state = _gapped(fold_fixture(EVEN_COMPLETE))
        db_path = tmp_path / "game.db"
        persister = CsaGamePersister(SqliteCsaRecordStore(db_path), REPLAYER)
        try:
            failures = persister.persist_finished([RunView(worker_idx=0, state=state, path=Path("x-events.jsonl"))])
            assert persister.persisted_count == 1, "marked done: the log will not grow the moves back"
            assert len(failures) == 1
            assert failures[0].ply == 5
            assert "contiguous" in failures[0].reason
        finally:
            persister.close()

    def test_the_gap_check_also_guards_the_csa_export(self) -> None:
        """Both writers go through `export_game`, so both are covered.

        The check first lived in the persister alone, which left `csa export` —
        the CLI that writes `.csa` files — free to emit the same gapped kifu.
        "Never writes an unverified game" is `export_game`'s promise, so the
        check belongs there rather than at one of its two call sites.
        """
        state = _gapped(fold_fixture(EVEN_COMPLETE))
        exported, failures = export_games(state.games, REPLAYER)
        assert exported == []
        assert len(failures) == 1
        assert failures[0].ply == 5

    def test_a_clean_game_still_persists(self, tmp_path: Path) -> None:
        # The gate is per game, not per run: the counters the run keeps cannot say
        # *which* game is short, and would block clean games sharing a damaged run.
        db_path, persister, failures = self._persist(tmp_path, EVEN_COMPLETE)
        try:
            assert failures == []
            assert persister.persisted_count == 1
        finally:
            persister.close()

    def test_a_store_failure_leaves_the_game_for_the_next_poll(self, tmp_path: Path) -> None:
        """A derived store must not take the watcher down, nor swallow the game.

        The narrow `except` this replaced let SQLAlchemy's `OperationalError`
        ("database is locked") escape the poll loop and stop the JSONL watcher
        and the HTTP API with it.
        """

        class LockedStore:
            def persist(self, record: object) -> None:
                raise CsaRecordStoreRetryableError("database is locked")

            def close(self) -> None:
                return None

        persister = CsaGamePersister(LockedStore(), REPLAYER)
        view = RunView(worker_idx=0, state=fold_fixture(EVEN_COMPLETE), path=Path("x-events.jsonl"))
        persister.persist_finished([view])
        assert persister.persisted_count == 0, "not marked done, so it can be retried"
        assert persister.has_deferred, (
            "the run has finished and will never change again, so the poll loop has to be "
            "told to hand it back; otherwise 'the next poll retries' is a promise nothing keeps"
        )

    def test_a_game_that_cannot_be_replayed_is_reported_and_skipped(self, tmp_path: Path) -> None:
        db_path, persister, failures = self._persist(tmp_path, ILLEGAL_MOVE)
        try:
            assert len(failures) == 1
            assert failures[0].ply == 5
        finally:
            persister.close()

    def test_the_stored_record_reads_back_with_its_csa_attributes(self, tmp_path: Path) -> None:
        from shogiarena._core.contexts.dashboard.adapters.game_repository import load_game_record

        db_path, persister, _ = self._persist(tmp_path, ENGINE_DEATH)
        persister.close()
        state = fold_fixture(ENGINE_DEATH)
        game_id = state.games[-1].game_id
        restored = load_game_record(db_path, game_name=csa_game_key(state.run_id, game_id))
        assert restored is not None
        assert restored.metadata.attributes["csa_run_id"] == ENGINE_DEATH
        assert restored.metadata.attributes["csa_server_game_id"] == game_id
        assert restored.metadata.start_date is not None
        assert restored.metadata.end_date is not None
        assert restored.move_count == state.games[-1].current_ply
        games = build_games_list_raw_payload(db_path, limit=10, offset=0, search_query=None)["games"]
        assert games[0]["start_time"] == restored.metadata.start_date
        assert games[0]["end_time"] == restored.metadata.end_date
        payload = build_game_detail_payload(
            record=restored,
            game_id=csa_game_key(state.run_id, game_id),
            logger=logging.getLogger(__name__),
        )
        assert payload["game_id"] == game_id

    def test_same_server_game_id_from_two_runs_survives_in_the_database(self, tmp_path: Path) -> None:
        from shogiarena._core.contexts.dashboard.adapters.game_repository import load_game_record

        first = fold_fixture(EVEN_COMPLETE)
        second = replace(first, run_id="second-run")
        db_path = tmp_path / "game.db"
        persister = CsaGamePersister(SqliteCsaRecordStore(db_path), REPLAYER)
        persister.persist_finished(
            [
                RunView(worker_idx=0, state=first, path=Path("first-events.jsonl")),
                RunView(worker_idx=1, state=second, path=Path("second-events.jsonl")),
            ]
        )
        persister.close()

        server_game_id = first.games[-1].game_id
        first_record = load_game_record(db_path, game_name=csa_game_key(first.run_id, server_game_id))
        second_record = load_game_record(db_path, game_name=csa_game_key(second.run_id, server_game_id))
        assert first_record is not None
        assert second_record is not None
        assert first_record.metadata.attributes["csa_server_game_id"] == server_game_id
        assert second_record.metadata.attributes["csa_server_game_id"] == server_game_id
