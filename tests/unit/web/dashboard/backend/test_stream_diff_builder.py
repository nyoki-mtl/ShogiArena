from __future__ import annotations

from collections.abc import Mapping

from shogiarena._core.contexts.dashboard.application.worker.stream_diffs.builder import build_game_diff_envelopes
from shogiarena._core.shared.kernel.snapshots import GameSnapshot


def _extract_gid(payload: Mapping[str, object]) -> str | None:
    value = payload.get("gid")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _get_worker_snapshot(_worker_idx: int) -> GameSnapshot | None:
    return None


def test_build_game_diff_compacts_engine_status_for_engine_status_diff() -> None:
    out = build_game_diff_envelopes(
        worker_idx=0,
        payload={
            "gid": "g1",
            "type": "handshake_log",
            "engine_status": {
                "black": {
                    "state": "ready",
                    "io_tail": [{"dir": "out", "line": "usiok", "ts": 1}],
                    "updated_at_ms": 101,
                },
                "white": {
                    "state": "waiting_for_bestmove",
                    "io_tail": [{"dir": "out", "line": "bestmove 7g7f", "ts": 2}],
                    "updated_at_ms": 202,
                },
            },
        },
        extract_gid=_extract_gid,
        assignment_rev=3,
        next_move_seq_by_gid={},
        get_worker_snapshot=_get_worker_snapshot,
        min_clock_publish_interval_ms=200,
        last_clock_publish_at={},
        last_engine_state_signature={},
    )

    assert len(out) == 1
    topic, payload = out[0]
    assert topic == "live.game.g1.engine_status.diff"
    assert payload["gid"] == "g1"
    assert payload["assignment_rev"] == 3
    assert payload["game_epoch"] == 3
    assert payload["engine_status"] == {
        "black": {"state": "ready", "io_tail": [], "updated_at_ms": 101},
        "white": {"state": "waiting_for_bestmove", "io_tail": [], "updated_at_ms": 202},
    }


def test_build_game_diff_suppresses_redundant_engine_state_diffs() -> None:
    last_engine_state_signature: dict[str, tuple[str, str]] = {}
    next_move_seq_by_gid: dict[str, int] = {}
    payload = {
        "gid": "g2",
        "type": "engine_io",
        "engine_status": {
            "black": {
                "state": "ready",
                "io_tail": [{"dir": "out", "line": "info depth 1", "ts": 10}],
                "updated_at_ms": 111,
            },
            "white": {
                "state": "ready",
                "io_tail": [{"dir": "out", "line": "info depth 2", "ts": 11}],
                "updated_at_ms": 222,
            },
        },
    }

    first = build_game_diff_envelopes(
        worker_idx=0,
        payload=payload,
        extract_gid=_extract_gid,
        assignment_rev=1,
        next_move_seq_by_gid=next_move_seq_by_gid,
        get_worker_snapshot=_get_worker_snapshot,
        min_clock_publish_interval_ms=200,
        last_clock_publish_at={},
        last_engine_state_signature=last_engine_state_signature,
    )
    second = build_game_diff_envelopes(
        worker_idx=0,
        payload={
            "gid": "g2",
            "type": "engine_io",
            "engine_status": {
                "black": {
                    "state": "ready",
                    "io_tail": [{"dir": "out", "line": "info depth 3", "ts": 12}],
                    "updated_at_ms": 333,
                },
                "white": {
                    "state": "ready",
                    "io_tail": [{"dir": "out", "line": "info depth 4", "ts": 13}],
                    "updated_at_ms": 444,
                },
            },
        },
        extract_gid=_extract_gid,
        assignment_rev=1,
        next_move_seq_by_gid=next_move_seq_by_gid,
        get_worker_snapshot=_get_worker_snapshot,
        min_clock_publish_interval_ms=200,
        last_clock_publish_at={},
        last_engine_state_signature=last_engine_state_signature,
    )

    assert len(first) == 1
    assert first[0][0] == "live.game.g2.engine_status.diff"
    assert "engine_status" in first[0][1]
    assert last_engine_state_signature["g2"] == ("ready", "ready")
    assert second == []


def test_build_game_diff_omits_engine_status_on_move_progress() -> None:
    next_move_seq_by_gid: dict[str, int] = {}
    out = build_game_diff_envelopes(
        worker_idx=0,
        payload={
            "gid": "g3",
            "type": "move_progress",
            "current_ply": 1,
            "move": "7g7f",
            "sfen": "sfen ...",
            "engine_status": {
                "black": {"state": "waiting_for_bestmove", "io_tail": [], "updated_at_ms": 301},
                "white": {"state": "ready", "io_tail": [], "updated_at_ms": 302},
            },
        },
        extract_gid=_extract_gid,
        assignment_rev=4,
        next_move_seq_by_gid=next_move_seq_by_gid,
        get_worker_snapshot=_get_worker_snapshot,
        min_clock_publish_interval_ms=200,
        last_clock_publish_at={},
        last_engine_state_signature={},
    )

    assert any(topic == "live.game.g3.moves.diff" for topic, _payload in out)
    assert all(topic != "live.game.g3.engine_status.diff" for topic, _payload in out)
    assert all(topic != "live.game.g3.meta.diff" for topic, _payload in out)
    moves_payload = next(payload for topic, payload in out if topic == "live.game.g3.moves.diff")
    assert moves_payload["move_seq"] == 1
    assert moves_payload["game_epoch"] == 4


def test_build_game_diff_routes_clock_payload_to_clock_diff() -> None:
    out = build_game_diff_envelopes(
        worker_idx=0,
        payload={
            "gid": "g4",
            "type": "clock_start",
            "active": "black",
            "black_remain_ms": 5000,
            "white_remain_ms": 5000,
            "started_at_ms": 123456,
        },
        extract_gid=_extract_gid,
        assignment_rev=5,
        next_move_seq_by_gid={},
        get_worker_snapshot=_get_worker_snapshot,
        min_clock_publish_interval_ms=200,
        last_clock_publish_at={},
        last_engine_state_signature={},
    )

    assert out == [
        (
            "live.game.g4.clock.diff",
            {
                "gid": "g4",
                "assignment_rev": 5,
                "game_epoch": 5,
                "clock": {
                    "active": "black",
                    "black_remain_ms": 5000,
                    "white_remain_ms": 5000,
                    "started_at_ms": 123456,
                },
                "type": "clock_start",
            },
        )
    ]


def test_build_game_diff_routes_meta_payload_to_meta_diff() -> None:
    out = build_game_diff_envelopes(
        worker_idx=0,
        payload={
            "gid": "g5",
            "type": "meta",
            "game_result": "BLACK_WIN",
            "meta": {"winner": "black"},
        },
        extract_gid=_extract_gid,
        assignment_rev=6,
        next_move_seq_by_gid={},
        get_worker_snapshot=_get_worker_snapshot,
        min_clock_publish_interval_ms=200,
        last_clock_publish_at={},
        last_engine_state_signature={},
    )

    assert (
        "live.game.g5.moves.diff",
        {
            "gid": "g5",
            "assignment_rev": 6,
            "game_epoch": 6,
            "game_result": "BLACK_WIN",
            "move_seq": 1,
        },
    ) in out
    assert (
        "live.game.g5.meta.diff",
        {
            "gid": "g5",
            "assignment_rev": 6,
            "game_epoch": 6,
            "game_result": "BLACK_WIN",
            "meta": {"winner": "black"},
        },
    ) in out


def test_build_game_diff_increments_move_seq_for_same_gid() -> None:
    next_move_seq_by_gid: dict[str, int] = {}

    first = build_game_diff_envelopes(
        worker_idx=0,
        payload={"gid": "g6", "type": "move_progress", "current_ply": 1, "move": "7g7f"},
        extract_gid=_extract_gid,
        assignment_rev=9,
        next_move_seq_by_gid=next_move_seq_by_gid,
        get_worker_snapshot=_get_worker_snapshot,
        min_clock_publish_interval_ms=200,
        last_clock_publish_at={},
        last_engine_state_signature={},
    )
    second = build_game_diff_envelopes(
        worker_idx=0,
        payload={"gid": "g6", "type": "move_progress", "current_ply": 2, "move": "3c3d"},
        extract_gid=_extract_gid,
        assignment_rev=9,
        next_move_seq_by_gid=next_move_seq_by_gid,
        get_worker_snapshot=_get_worker_snapshot,
        min_clock_publish_interval_ms=200,
        last_clock_publish_at={},
        last_engine_state_signature={},
    )

    first_moves = next(payload for topic, payload in first if topic == "live.game.g6.moves.diff")
    second_moves = next(payload for topic, payload in second if topic == "live.game.g6.moves.diff")
    assert first_moves["move_seq"] == 1
    assert second_moves["move_seq"] == 2
