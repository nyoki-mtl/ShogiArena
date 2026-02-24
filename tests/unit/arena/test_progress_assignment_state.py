from __future__ import annotations

from shogiarena.arena.orchestrators.progress import ProgressHub, ProgressState


def _new_hub() -> ProgressHub:
    return ProgressHub(
        api_server=None,
        preassign_worker=lambda game_id, num_workers, game_to_worker, worker_busy: 0,
    )


def test_game_assigned_marks_engines_as_queued_for_new_game() -> None:
    hub = _new_hub()
    state = ProgressState(
        num_workers=1,
        game_to_worker={1: 0},
        worker_busy={0},
        worker_snapshots={
            0: {
                "_generation": 1,
                "game_id": "old-game",
                "engine_status": {
                    "black": {"state": "ready", "io_tail": [{"dir": "out", "line": "go", "ts": 1}], "updated_at_ms": 1},
                    "white": {
                        "state": "ready",
                        "io_tail": [{"dir": "in", "line": "bestmove", "ts": 1}],
                        "updated_at_ms": 1,
                    },
                },
            }
        },
    )
    progress = {
        "type": "game_assigned",
        "game_id": "new-game",
        "initial_sfen": "startpos",
        "black_name": "black",
        "white_name": "white",
    }

    diff = hub._handle_game_assigned(
        worker_idx=0,
        current_gen=1,
        progress=progress,
        state=state,
        game_id_num=1,
    )

    assert diff is not None
    status = state.worker_snapshots[0]["engine_status"]
    assert status["black"]["state"] == "queued"
    assert status["white"]["state"] == "queued"
    assert status["black"]["io_tail"] == []
    assert status["white"]["io_tail"] == []


def test_game_assigned_keeps_existing_status_for_same_game() -> None:
    hub = _new_hub()
    existing_tail = [{"dir": "out", "line": "isready", "ts": 10}]
    state = ProgressState(
        num_workers=1,
        game_to_worker={1: 0},
        worker_busy={0},
        worker_snapshots={
            0: {
                "_generation": 1,
                "game_id": "same-game",
                "engine_status": {
                    "black": {"state": "waiting_for_readyok", "io_tail": list(existing_tail), "updated_at_ms": 10},
                    "white": {"state": "ready", "io_tail": list(existing_tail), "updated_at_ms": 10},
                },
            }
        },
    )
    progress = {
        "type": "game_assigned",
        "game_id": "same-game",
        "initial_sfen": "startpos",
        "black_name": "black",
        "white_name": "white",
    }

    _ = hub._handle_game_assigned(
        worker_idx=0,
        current_gen=1,
        progress=progress,
        state=state,
        game_id_num=1,
    )

    status = state.worker_snapshots[0]["engine_status"]
    assert status["black"]["state"] == "waiting_for_readyok"
    assert status["white"]["state"] == "ready"
    assert status["black"]["io_tail"] == existing_tail
    assert status["white"]["io_tail"] == existing_tail


def test_game_assigned_resets_snapshot_clock_fields_for_new_game() -> None:
    hub = _new_hub()
    state = ProgressState(
        num_workers=1,
        game_to_worker={1: 0},
        worker_busy={0},
        worker_snapshots={
            0: {
                "_generation": 1,
                "game_id": "old-game",
                "initial_sfen": "startpos",
                "black_name": "old-black",
                "white_name": "old-white",
                "moves": ["7g7f", "3c3d"],
                "_clock_active": "black",
                "_black_remain_ms": 120_000,
                "_white_remain_ms": 119_000,
                "_clock_started_at_ms": 999_999,
            }
        },
    )
    progress = {
        "type": "game_assigned",
        "game_id": "new-game",
        "initial_sfen": "startpos",
        "black_name": "new-black",
        "white_name": "new-white",
        "time_control_black": "t300000+i2000",
        "time_control_white": "t300000+i2000",
    }

    _ = hub._handle_game_assigned(
        worker_idx=0,
        current_gen=1,
        progress=progress,
        state=state,
        game_id_num=1,
    )

    snapshot = state.worker_snapshots[0]
    assert snapshot["game_id"] == "new-game"
    assert snapshot["black_name"] == "new-black"
    assert snapshot["white_name"] == "new-white"
    assert snapshot["moves"] == []
    assert "_clock_active" not in snapshot
    assert "_black_remain_ms" not in snapshot
    assert "_white_remain_ms" not in snapshot
    assert "_clock_started_at_ms" not in snapshot
    assert snapshot["time_control_black"] == "t300000+i2000"
    assert snapshot["time_control_white"] == "t300000+i2000"


def test_game_assigned_marks_unstarted_same_game_status_as_queued() -> None:
    hub = _new_hub()
    state = ProgressState(
        num_workers=1,
        game_to_worker={1: 0},
        worker_busy={0},
        worker_snapshots={
            0: {
                "_generation": 1,
                "game_id": "same-game",
                "initial_sfen": "startpos",
                "engine_status": {
                    "black": {"state": "waiting_for_usiok", "io_tail": [], "updated_at_ms": 1},
                    "white": {"state": "waiting_for_usiok", "io_tail": [], "updated_at_ms": 1},
                },
            }
        },
    )
    progress = {
        "type": "game_assigned",
        "game_id": "same-game",
        "initial_sfen": "startpos",
        "black_name": "black",
        "white_name": "white",
    }

    _ = hub._handle_game_assigned(
        worker_idx=0,
        current_gen=1,
        progress=progress,
        state=state,
        game_id_num=1,
    )

    status = state.worker_snapshots[0]["engine_status"]
    assert status["black"]["state"] == "queued"
    assert status["white"]["state"] == "queued"
