from __future__ import annotations

from shogiarena._core.contexts.game_session.application.progress.consumption import ProgressState
from shogiarena._core.contexts.game_session.application.progress.consumption_event_handlers import (
    handle_engine_io,
    handle_engine_state,
    handle_handshake_log,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_models import (
    EngineStatusModel,
    WorkerSnapshotModel,
)


def _build_snapshot(*, black_state: str = "ready", white_state: str = "ready") -> WorkerSnapshotModel:
    snapshot = WorkerSnapshotModel(
        game_id="g1",
        initial_sfen="startpos",
        black_name="black",
        white_name="white",
        current_ply=0,
        sfen="startpos",
    )
    snapshot.engine_status = {
        "black": EngineStatusModel(state=black_state, io_tail=[], updated_at_ms=100),
        "white": EngineStatusModel(state=white_state, io_tail=[], updated_at_ms=100),
    }
    return snapshot


def _build_state(snapshot: WorkerSnapshotModel) -> ProgressState:
    return ProgressState(
        num_workers=1,
        game_to_worker={1: 0},
        worker_busy={0},
        worker_snapshots={0: snapshot},
        worker_generation={0: 1},
    )


def test_handle_engine_io_omits_diff_when_state_is_unchanged() -> None:
    snapshot = _build_snapshot(black_state="ready")
    state = _build_state(snapshot)

    diff = handle_engine_io(
        worker_idx=0,
        current_gen=1,
        progress={
            "type": "engine_io",
            "game_id": "g1",
            "role": "black",
            "direction": "out",
            "line": "info depth 1",
            "state": "ready",
            "ts": 123,
        },
        state=state,
        game_id_num=1,
    )

    assert diff is None
    assert len(snapshot.engine_status["black"].io_tail) == 1
    assert snapshot.engine_status["black"].io_tail[0].line == "info depth 1"


def test_handle_engine_io_emits_diff_when_state_transitions() -> None:
    snapshot = _build_snapshot(black_state="ready")
    state = _build_state(snapshot)

    diff = handle_engine_io(
        worker_idx=0,
        current_gen=1,
        progress={
            "type": "engine_io",
            "game_id": "g1",
            "role": "black",
            "direction": "out",
            "line": "bestmove 7g7f",
            "state": "waiting_for_bestmove",
            "ts": 456,
        },
        state=state,
        game_id_num=1,
    )

    assert diff is not None
    assert diff["type"] == "engine_io"
    assert diff["engine_status"]["black"]["state"] == "waiting_for_bestmove"


def test_handle_engine_io_omits_diff_when_state_is_missing() -> None:
    snapshot = _build_snapshot(black_state="ready")
    state = _build_state(snapshot)

    diff = handle_engine_io(
        worker_idx=0,
        current_gen=1,
        progress={
            "type": "engine_io",
            "game_id": "g1",
            "role": "black",
            "direction": "out",
            "line": "info depth 2",
            "ts": 789,
        },
        state=state,
        game_id_num=1,
    )

    assert diff is None


def test_handle_engine_state_emits_diff_on_transition() -> None:
    snapshot = _build_snapshot(black_state="ready")
    state = _build_state(snapshot)

    diff = handle_engine_state(
        worker_idx=0,
        current_gen=1,
        progress={
            "type": "engine_state",
            "game_id": "g1",
            "role": "black",
            "state": "waiting_for_bestmove",
            "ts": 123,
        },
        state=state,
        game_id_num=1,
    )

    assert diff is not None
    assert diff["type"] == "engine_io"
    assert diff["engine_status"]["black"]["state"] == "waiting_for_bestmove"
    # Lifecycle-driven badge carries no raw line, so the io tail is untouched.
    assert snapshot.engine_status["black"].io_tail == []


def test_handle_engine_state_appends_kickoff_command_to_io_tail() -> None:
    snapshot = _build_snapshot(black_state="ready")
    state = _build_state(snapshot)

    handle_engine_state(
        worker_idx=0,
        current_gen=1,
        progress={
            "type": "engine_state",
            "game_id": "g1",
            "role": "black",
            "state": "ready",
            "ts": 5,
            "direction": "out",
            "line": "usinewgame",
        },
        state=state,
        game_id_num=1,
    )

    # The kickoff command rides along so the card UI can detect game start without the raw flood.
    tail = snapshot.engine_status["black"].io_tail
    assert [entry.line for entry in tail] == ["usinewgame"]
    assert tail[0].dir == "out"


def test_handle_engine_state_omits_diff_when_state_unchanged() -> None:
    snapshot = _build_snapshot(black_state="ready")
    state = _build_state(snapshot)

    diff = handle_engine_state(
        worker_idx=0,
        current_gen=1,
        progress={"type": "engine_state", "game_id": "g1", "role": "black", "state": "ready", "ts": 200},
        state=state,
        game_id_num=1,
    )

    assert diff is None


def test_handle_handshake_log_emits_only_on_state_transition() -> None:
    snapshot = _build_snapshot(black_state="waiting_for_usiok")
    state = _build_state(snapshot)

    first = handle_handshake_log(
        worker_idx=0,
        current_gen=1,
        progress={
            "type": "handshake_log",
            "game_id": "g1",
            "role": "black",
            "direction": "out",
            "line": "usiok",
            "state": "ready",
            "ts": 1000,
        },
        state=state,
        game_id_num=1,
    )
    second = handle_handshake_log(
        worker_idx=0,
        current_gen=1,
        progress={
            "type": "handshake_log",
            "game_id": "g1",
            "role": "black",
            "direction": "out",
            "line": "readyok",
            "state": "ready",
            "ts": 1001,
        },
        state=state,
        game_id_num=1,
    )

    assert first is not None
    assert first["engine_status"]["black"]["state"] == "ready"
    assert second is None
