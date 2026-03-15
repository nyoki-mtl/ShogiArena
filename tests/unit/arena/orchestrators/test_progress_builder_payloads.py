"""Tests for snapshot payload builders (to_ws_state_diff / to_game_assigned_payload)."""

from __future__ import annotations

from typing import cast

from shogiarena._core.contexts.game_session.application.progress.events import (
    ClockIncrementEvent,
    ClockStartEvent,
    GameAssignedEvent,
    MoveProgressEvent,
    parse_progress_event,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_models import (
    EngineIoTailModel,
    EngineStatusModel,
    WorkerSnapshotModel,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_payload_builders import (
    to_game_assigned_payload,
    to_ws_moves_diff,
    to_ws_state_diff,
)


def _snapshot() -> WorkerSnapshotModel:
    return WorkerSnapshotModel(
        game_id="model-game",
        initial_sfen="startpos",
        black_name="black",
        white_name="white",
        current_ply=3,
        sfen="startpos moves 1",
        time_control_black="t60+0.5",
        time_control_white={"tc": "t60+0.5"},
    )


class TestSnapshotPayloadBuilders:
    def test_move_progress_uses_model_game_id_when_missing(self) -> None:
        snapshot = _snapshot()
        event = cast(MoveProgressEvent, parse_progress_event({"type": "move_progress", "move": "7g7f"}))

        payload = to_ws_state_diff(event, snapshot)

        assert payload["type"] == "move_progress"
        assert payload["game_id"] == "model-game"

    def test_clock_start_uses_event_game_id(self) -> None:
        snapshot = WorkerSnapshotModel(
            game_id="",
            initial_sfen="startpos",
            black_name="black",
            white_name="white",
            current_ply=0,
            sfen="startpos",
            time_control_black="t60+0.5",
            time_control_white={"tc": "t60+0.5"},
        )
        event = cast(
            ClockStartEvent,
            parse_progress_event(
                {
                    "type": "clock_start",
                    "game_id": "fb-1",
                    "black_remain_ms": "1000",
                    "white_remain_ms": "1000",
                }
            ),
        )

        payload = to_ws_state_diff(event, snapshot)

        assert payload["type"] == "clock_start"
        assert payload["game_id"] == "fb-1"

    def test_clock_increment_includes_common_fields(self) -> None:
        snapshot = _snapshot()
        event = cast(
            ClockIncrementEvent,
            parse_progress_event(
                {
                    "type": "clock_increment",
                    "side": "black",
                    "applied_increment_ms": 1,
                    "occurred_at_ms": 10,
                }
            ),
        )

        payload = to_ws_state_diff(event, snapshot)

        assert payload["type"] == "clock_increment"
        assert payload["side"] == "black"
        assert payload["applied_increment_ms"] == 1
        assert payload["occurred_at_ms"] == 10
        assert payload["game_id"] == "model-game"

    def test_game_assigned_uses_model_when_id_missing(self) -> None:
        snapshot = WorkerSnapshotModel(
            game_id="assigned-game",
            initial_sfen="startpos",
            black_name="black",
            white_name="white",
            current_ply=0,
            sfen="startpos",
            time_control_black="tc-black",
        )
        event = cast(
            GameAssignedEvent,
            parse_progress_event(
                {"type": "game_assigned", "initial_sfen": "startpos", "black_name": "b2", "white_name": "w2"}
            ),
        )

        payload = to_game_assigned_payload(event, snapshot)

        assert payload["type"] == "game_assigned"
        assert payload["game_id"] == "assigned-game"
        assert payload["initial_sfen"] == "startpos"
        assert payload["time_control_black"] == "tc-black"

    def test_game_assigned_serializes_engine_status_as_json_object(self) -> None:
        snapshot = _snapshot()
        snapshot.engine_status["black"] = EngineStatusModel(
            state="searching",
            io_tail=[EngineIoTailModel(dir="out", line="info depth 1", ts=42, state="searching")],
            updated_at_ms=99,
        )
        event = cast(
            GameAssignedEvent,
            parse_progress_event(
                {"type": "game_assigned", "initial_sfen": "startpos", "black_name": "b2", "white_name": "w2"}
            ),
        )

        payload = to_game_assigned_payload(event, snapshot)

        assert payload["engine_status"] == {
            "black": {
                "state": "searching",
                "io_tail": [{"dir": "out", "line": "info depth 1", "ts": 42, "state": "searching"}],
                "updated_at_ms": 99,
            }
        }

    def test_engine_log_diff_emits_engine_status_and_role(self) -> None:
        snapshot = _snapshot()
        event = parse_progress_event(
            {
                "type": "engine_io",
                "game_id": "event-game",
                "role": "black",
                "direction": "out",
                "line": "info depth 1",
                "ts": 42,
                "state": "searching",
            }
        )

        payload = to_ws_moves_diff(event, snapshot)

        assert payload["type"] == "engine_io"
        assert payload["game_id"] == "event-game"
        assert payload["role"] == "black"
        assert payload["direction"] == "out"
        assert payload["line"] == "info depth 1"
        assert payload["ts"] == 42
        assert payload["state"] == "searching"
        assert payload["engine_status"] == {}
