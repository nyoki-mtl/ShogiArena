"""Progress event parser contract tests."""

from __future__ import annotations

import pytest

from shogiarena._core.contexts.game_session.application.progress.events import parse_progress_event
from shogiarena._core.contexts.game_session.application.progress.payload_parser import parse_move_progress


class TestParseProgressEvent:
    def test_parse_move_progress_normalizes_partial_fields(self) -> None:
        raw = {
            "type": "move_progress",
            "game_id": "100",
            "initial_sfen": "startpos",
            "move": "7g7f",
            "eval_cp": "42",
            "time_ms": 10.2,
            "is_latency_alert": "yes",
        }

        event = parse_progress_event(raw)

        assert event["type"] == "move_progress"
        assert event["game_id"] == "100"
        assert event["move"] == "7g7f"
        assert event["eval_cp"] == 42
        assert event["time_ms"] == 10
        assert event["is_latency_alert"] is True

    def test_parse_move_progress_ignores_legacy_latency_alert_key(self) -> None:
        event = parse_progress_event({"type": "move_progress", "game_id": "100", "latency_alert": "yes"})
        assert "is_latency_alert" not in event

    def test_parse_clock_increment_keeps_expected_keys(self) -> None:
        raw = {
            "type": "clock_increment",
            "game_id": "200",
            "side": "white",
            "applied_increment_ms": "5",
            "black_remain_ms": 100,
            "white_remain_ms": 90,
            "occurred_at_ms": 123,
        }

        event = parse_progress_event(raw)

        assert event["type"] == "clock_increment"
        assert event["game_id"] == "200"
        assert event["side"] == "white"
        assert event["applied_increment_ms"] == 5
        assert event["black_remain_ms"] == 100
        assert event["white_remain_ms"] == 90

    def test_parse_handshake_requires_role(self) -> None:
        with pytest.raises(ValueError, match="handshake_log event requires role"):
            parse_progress_event({"type": "handshake_log", "line": "x"})

    def test_parse_engine_io_requires_role(self) -> None:
        with pytest.raises(ValueError, match="engine_io event requires role"):
            parse_progress_event({"type": "engine_io", "line": "x"})

    def test_parse_unknown_type_rejected(self) -> None:
        with pytest.raises(ValueError, match="unsupported progress event type"):
            parse_progress_event({"type": "invalid", "game_id": "1"})

    def test_parse_game_assigned_requires_initial_sfen(self) -> None:
        with pytest.raises(ValueError, match="game_assigned event requires initial_sfen"):
            parse_progress_event(
                {
                    "type": "game_assigned",
                    "game_id": "1",
                    "black_name": "A",
                    "white_name": "B",
                }
            )


class TestParseEngineIoPayload:
    def test_parse_engine_io_event(self) -> None:
        raw = {
            "type": "engine_io",
            "game_id": "2",
            "role": "black",
            "direction": "in",
            "line": "usinewgame",
            "state": "ready",
            "ts": "777",
        }

        event = parse_progress_event(raw)
        assert event["type"] == "engine_io"
        assert event["role"] == "black"
        assert event["direction"] == "in"
        assert event["line"] == "usinewgame"
        assert event["ts"] == 777
        assert event["state"] == "ready"


class TestParseMoveProgressLegacy:
    def test_parse_move_progress_defaults_type_and_keeps_type(self) -> None:
        event = parse_move_progress({"game_id": "101", "move": "7g7f", "eval_cp": 12})

        assert event["type"] == "move_progress"
        assert event["game_id"] == "101"
        assert event["move"] == "7g7f"
        assert event["eval_cp"] == 12

    def test_parse_move_progress_rejects_non_move(self) -> None:
        with pytest.raises(ValueError, match="payload type mismatch for parse_move_progress"):
            parse_move_progress({"type": "clock_start", "game_id": "101"})
