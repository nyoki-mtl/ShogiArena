from __future__ import annotations

from shogiarena._core.contexts.game_session.application.progress.events import parse_progress_event
from shogiarena._core.contexts.game_session.application.progress.snapshot_models import (
    ClockModel,
    EngineIoTailModel,
    EngineStatusModel,
    WorkerSnapshotModel,
)
from shogiarena._core.contexts.game_session.application.progress.snapshot_payload_builders import (
    normalize_worker_snapshot_dto,
    to_worker_snapshot_dto,
    to_ws_state_diff,
)
from shogiarena._core.shared.kernel.game_results import GameResult


def _snapshot() -> WorkerSnapshotModel:
    return WorkerSnapshotModel(
        game_id="game-123",
        initial_sfen="startpos",
        black_name="black",
        white_name="white",
        current_ply=2,
        sfen="startpos moves 3",
        moves=["7g7f", "3c3d"],
        ki2_moves=["7六歩", "3四歩"],
        eval_black=[12, 11],
        eval_white=[-12, -11],
        nodes_values=[100, 200],
        depth_values=[10, 12],
        seldepth_values=[11, 13],
        move_times_ms=[15, 16],
        wall_times_ms=[17, 18],
        latency_deltas_ms=[1, 2],
        latency_alerts=[True, False],
        game_result=GameResult.BLACK_WIN,
        clock=ClockModel(
            active="black",
            black_remain_ms=120000,
            white_remain_ms=118000,
            started_at_ms=170000,
            occurred_at_ms=170500,
        ),
        generation=4,
        time_control_black="t3000+10",
        time_control_white={"time_control": "t3000+10"},
        meta={"event": "snapshot", "moves": 2},
        engine_status={
            "black": EngineStatusModel(
                state="ready",
                io_tail=[
                    EngineIoTailModel(dir="in", line="usinewgame", ts=111, state="waiting_for_readyok"),
                    EngineIoTailModel(dir="out", line="readyok", ts=112),
                ],
                updated_at_ms=170000,
            ),
            "white": EngineStatusModel(state="ready", io_tail=[], updated_at_ms=170001),
        },
    )


class TestWorkerSnapshotDto:
    def test_to_worker_snapshot_dto_golden(self) -> None:
        model = _snapshot()

        dto = to_worker_snapshot_dto(model)

        assert dto["game_id"] == "game-123"
        assert dto["generation"] == 4
        assert dto["clock_active"] == "black"
        assert dto["black_remain_ms"] == 120000
        assert dto["white_remain_ms"] == 118000
        assert dto["time_control_black"] == "t3000+10"
        assert dto["time_control_white"] == {"time_control": "t3000+10"}
        assert dto["meta"] == {"event": "snapshot", "moves": 2}
        assert dto["engine_status"]["black"]["state"] == "ready"
        assert dto["engine_status"]["black"]["io_tail"] == [
            {"dir": "in", "line": "usinewgame", "ts": 111, "state": "waiting_for_readyok"},
            {"dir": "out", "line": "readyok", "ts": 112},
        ]
        assert dto["current_ply"] == 2
        assert dto["game_result"] == "BLACK_WIN"

    def test_normalize_worker_snapshot_dto_accepts_mapping(self) -> None:
        dto = normalize_worker_snapshot_dto(
            {
                "game_id": "g1",
                "initial_sfen": "startpos",
                "black_name": "black",
                "white_name": "white",
                "moves": ["7g7f"],
                "ki2_moves": ["7六歩"],
                "eval_black": [12],
                "eval_white": [],
                "nodes_values": [100],
                "depth_values": [10],
                "seldepth_values": [11],
                "move_times_ms": [15],
                "wall_times_ms": [17],
                "latency_deltas_ms": [1],
                "latency_alerts": [False],
                "current_ply": 1,
                "sfen": "startpos moves 7g7f",
                "clock_active": "black",
                "black_remain_ms": 120000,
                "white_remain_ms": 118000,
                "engine_status": {
                    "black": {"state": "ready", "io_tail": [], "updated_at_ms": 170000},
                },
            }
        )

        assert dto is not None
        assert dto["game_id"] == "g1"
        assert dto["clock_active"] == "black"
        assert dto["engine_status"]["black"]["state"] == "ready"

    def test_to_ws_state_diff_clock_start_does_not_fallback_to_model_time_control(self) -> None:
        model = _snapshot()
        event = parse_progress_event(
            {
                "type": "clock_start",
                "game_id": "game-123",
                "black_remain_ms": "150000",
                "white_remain_ms": "140000",
            }
        )

        diff = to_ws_state_diff(event, model)

        assert diff["type"] == "clock_start"
        assert diff["game_id"] == "game-123"
        assert "time_control_black" not in diff
        assert "time_control_white" not in diff

    def test_to_ws_state_diff_clock_increment_uses_event_time_control_only(self) -> None:
        model = _snapshot()
        event = parse_progress_event(
            {
                "type": "clock_increment",
                "game_id": "game-123",
                "black_remain_ms": "130000",
                "white_remain_ms": 120000,
                "occurred_at_ms": "170777",
            }
        )

        diff = to_ws_state_diff(event, model)

        assert diff["type"] == "clock_increment"
        assert diff["occurred_at_ms"] == 170777
        assert "time_control_black" not in diff
        assert "time_control_white" not in diff

    def test_to_ws_state_diff_move_includes_current_ply_and_game_result(self) -> None:
        model = _snapshot()
        event = parse_progress_event(
            {
                "type": "move_progress",
                "game_id": "game-123",
                "move": "2g2f",
                "game_result": "WHITE_WIN",
            }
        )
        model.current_ply = 3

        diff = to_ws_state_diff(event, model)

        assert diff["current_ply"] == 3
        assert diff["game_result"] == "WHITE_WIN"
        assert "engine_status" not in diff
