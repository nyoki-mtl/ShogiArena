from __future__ import annotations

from shogiarena._core.contexts.game_session.application.progress.consumption import ProgressState
from shogiarena._core.contexts.game_session.application.progress.consumption_event_handlers import handle_game_assigned
from shogiarena._core.contexts.game_session.application.progress.snapshot_models import (
    ClockModel,
    EngineIoTailModel,
    EngineStatusModel,
    WorkerSnapshotModel,
)


def _snapshot_with_status(
    *,
    game_id: str,
    initial_sfen: str = "startpos",
    generation: int = 1,
    black_name: str = "",
    white_name: str = "",
    moves: list[str] | None = None,
    black_state: str = "ready",
    white_state: str = "ready",
    black_tail: list[EngineIoTailModel] | None = None,
    white_tail: list[EngineIoTailModel] | None = None,
    black_updated_at: int = 1,
    white_updated_at: int = 1,
) -> WorkerSnapshotModel:
    return WorkerSnapshotModel(
        game_id=game_id,
        initial_sfen=initial_sfen,
        black_name=black_name or "black",
        white_name=white_name or "white",
        current_ply=0,
        sfen=initial_sfen,
        moves=moves or [],
        engine_status={
            "black": EngineStatusModel(state=black_state, io_tail=black_tail or [], updated_at_ms=black_updated_at),
            "white": EngineStatusModel(state=white_state, io_tail=white_tail or [], updated_at_ms=white_updated_at),
        },
        generation=generation,
    )


def test_game_assigned_marks_engines_as_queued_for_new_game() -> None:
    state = ProgressState(
        num_workers=1,
        game_to_worker={1: 0},
        worker_busy={0},
        worker_snapshots={
            0: _snapshot_with_status(
                game_id="old-game",
                black_tail=[EngineIoTailModel(dir="out", line="go", ts=1)],
                white_tail=[EngineIoTailModel(dir="in", line="bestmove", ts=1)],
            )
        },
    )
    progress = {
        "type": "game_assigned",
        "game_id": "new-game",
        "initial_sfen": "startpos",
        "black_name": "black",
        "white_name": "white",
    }

    diff = handle_game_assigned(
        worker_idx=0,
        current_gen=1,
        progress=progress,
        state=state,
        game_id_num=1,
        api_server=None,
    )

    assert diff is not None
    snapshot = state.worker_snapshots[0]
    status = snapshot.engine_status
    assert status["black"].state == "queued"
    assert status["white"].state == "queued"
    assert status["black"].io_tail == []
    assert status["white"].io_tail == []


def test_game_assigned_keeps_existing_status_for_same_game() -> None:
    existing_tail = [EngineIoTailModel(dir="out", line="isready", ts=10)]
    state = ProgressState(
        num_workers=1,
        game_to_worker={1: 0},
        worker_busy={0},
        worker_snapshots={
            0: _snapshot_with_status(
                game_id="same-game",
                black_state="waiting_for_readyok",
                white_state="ready",
                black_tail=[EngineIoTailModel(dir="out", line="isready", ts=10)],
                white_tail=[EngineIoTailModel(dir="out", line="isready", ts=10)],
                black_updated_at=10,
                white_updated_at=10,
                black_name="black",
                white_name="white",
            )
        },
    )
    progress = {
        "type": "game_assigned",
        "game_id": "same-game",
        "initial_sfen": "startpos",
        "black_name": "black",
        "white_name": "white",
    }

    _ = handle_game_assigned(
        worker_idx=0,
        current_gen=1,
        progress=progress,
        state=state,
        game_id_num=1,
        api_server=None,
    )

    snapshot = state.worker_snapshots[0]
    status = snapshot.engine_status
    assert status["black"].state == "waiting_for_readyok"
    assert status["white"].state == "ready"
    assert status["black"].io_tail == existing_tail
    assert status["white"].io_tail == existing_tail


def test_game_assigned_resets_snapshot_clock_fields_for_new_game() -> None:
    state = ProgressState(
        num_workers=1,
        game_to_worker={1: 0},
        worker_busy={0},
        worker_snapshots={
            0: WorkerSnapshotModel(
                game_id="old-game",
                initial_sfen="startpos",
                black_name="old-black",
                white_name="old-white",
                current_ply=2,
                sfen="startpos",
                moves=["7g7f", "3c3d"],
                clock={
                    "active": "black",
                    "black_remain_ms": 120_000,
                    "white_remain_ms": 119_000,
                    "started_at_ms": 999_999,
                    "occurred_at_ms": None,
                },
            )
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

    _ = handle_game_assigned(
        worker_idx=0,
        current_gen=1,
        progress=progress,
        state=state,
        game_id_num=1,
        api_server=None,
    )

    snapshot = state.worker_snapshots[0]
    assert snapshot.game_id == "new-game"
    assert snapshot.black_name == "new-black"
    assert snapshot.white_name == "new-white"
    assert snapshot.moves == []
    assert snapshot.clock == ClockModel()
    assert snapshot.time_control_black == "t300000+i2000"
    assert snapshot.time_control_white == "t300000+i2000"


def test_game_assigned_marks_unstarted_same_game_status_as_queued() -> None:
    state = ProgressState(
        num_workers=1,
        game_to_worker={1: 0},
        worker_busy={0},
        worker_snapshots={
            0: _snapshot_with_status(
                game_id="same-game",
                black_state="waiting_for_usiok",
                white_state="waiting_for_usiok",
            )
        },
    )
    progress = {
        "type": "game_assigned",
        "game_id": "same-game",
        "initial_sfen": "startpos",
        "black_name": "black",
        "white_name": "white",
    }

    _ = handle_game_assigned(
        worker_idx=0,
        current_gen=1,
        progress=progress,
        state=state,
        game_id_num=1,
        api_server=None,
    )

    status = state.worker_snapshots[0].engine_status
    assert status["black"].state == "queued"
    assert status["white"].state == "queued"
