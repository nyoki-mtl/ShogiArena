from __future__ import annotations

from shogiarena._core.contexts.dashboard.application.stream_topics import (
    parse_live_engine_io_target,
    parse_live_game_gid,
    topic_live_engine_io_snapshot,
    topic_live_game_analysis_diff,
    topic_live_game_clock_diff,
    topic_live_game_engine_status_diff,
    topic_live_game_meta_diff,
    topic_live_game_moves_diff,
    topic_live_game_snapshot,
)


def test_parse_live_game_gid_parses_snapshot_and_diff_topics() -> None:
    assert parse_live_game_gid("live.game.g1.snapshot") == "g1"
    assert parse_live_game_gid("live.game.g1.moves.diff") == "g1"
    assert parse_live_game_gid("live.game.g1.analysis.diff") == "g1"
    assert parse_live_game_gid("live.game.g1.meta.diff") == "g1"
    assert parse_live_game_gid("live.game.g1.clock.diff") == "g1"
    assert parse_live_game_gid("live.game.g1.engine_status.diff") == "g1"


def test_parse_live_game_gid_rejects_invalid_topics() -> None:
    assert parse_live_game_gid("live.summary.snapshot.tournament") is None
    assert parse_live_game_gid("live.game..snapshot") is None
    assert parse_live_game_gid("live.game.g1") is None
    assert parse_live_game_gid("live.game.g1.state.diff") is None
    assert parse_live_game_gid("live.game.g1.diff") is None


def test_parse_live_engine_io_target_parses_and_validates_role() -> None:
    assert parse_live_engine_io_target("live.engine.g1.black.io.snapshot") == ("g1", "black")
    assert parse_live_engine_io_target("live.engine.round.1.g1.white.io.diff") == ("round.1.g1", "white")
    assert parse_live_engine_io_target("live.engine.g1.green.io.snapshot") is None
    assert parse_live_engine_io_target("live.engine.g1.black") is None


def test_topic_builders_return_expected_topic_strings() -> None:
    assert topic_live_game_snapshot("g1") == "live.game.g1.snapshot"
    assert topic_live_game_moves_diff("g1") == "live.game.g1.moves.diff"
    assert topic_live_game_analysis_diff("g1") == "live.game.g1.analysis.diff"
    assert topic_live_game_meta_diff("g1") == "live.game.g1.meta.diff"
    assert topic_live_game_clock_diff("g1") == "live.game.g1.clock.diff"
    assert topic_live_game_engine_status_diff("g1") == "live.game.g1.engine_status.diff"
    assert topic_live_engine_io_snapshot("g1", "black") == "live.engine.g1.black.io.snapshot"
