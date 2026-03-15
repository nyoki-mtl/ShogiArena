"""Boundary parser tests for tournament runner and OpenBench state."""

from __future__ import annotations

import pytest

from shogiarena._core.contexts.game_session.adapters.openbench.client import OpenBenchClient
from shogiarena._core.contexts.game_session.adapters.openbench.client_types import OpenBenchClientConfig
from shogiarena._core.interfaces.boundaries.parsers.session_context import parse_session_context_snapshot_boundary
from shogiarena._core.shared.kernel.boundary_parsers.openbench import parse_openbench_client_state_boundary
from shogiarena._core.shared.kernel.boundary_parsers.runner_state_payloads.parsers import (
    parse_spsa_index_boundary,
    parse_spsa_run_state_boundary,
    parse_tournament_run_state_boundary,
)
from shogiarena._core.shared.kernel.exceptions import ContractParseError


def test_parse_tournament_run_state_boundary_accepts_valid_payload_with_nested_openbench() -> None:
    payload = {
        "config": {"experiment_name": "boundary-test"},
        "schedule_hash": "abc",
        "total_games": 1,
        "completed_game_ids": ["g01"],
        "cancelled_game_ids": [],
        "completed_games_count": 1,
        "cancelled_games_count": 0,
        "original_total_games": 1,
        "game_display_order": {"g01": "1"},
        "is_finished": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:01:00Z",
        "sprt_state": {
            "elo0": 0.0,
            "elo1": 0.0,
            "alpha": -1.0,
            "beta": 3.0,
            "wins": 1,
            "draws": 0,
            "losses": 0,
            "games_played": 1,
            "llr": 0.1,
        },
        "openbench_state": {
            "submitted": {
                "losses": "2",
                "draws": 1,
                "wins": 0,
                "ll": 0,
                "ld": 0,
                "dd": 0,
                "dw": 0,
                "ww": 0,
                "crashes": 0,
                "timelosses": 0,
                "illegals": 0,
            },
            "last_synced_games": "3",
            "target_test_id": "12",
            "claimed_test_id": 99,
            "result_id": 7,
            "blacklist": [1, "2", 3.0],
        },
        "completed_game_summaries": {
            "g01": {
                "game_result": "BLACK_WIN",
                "total_plies": "42",
                "start_time": "2026-01-01T00:00:30Z",
                "end_time": "2026-01-01T00:00:55Z",
            }
        },
        "game_instance_overrides": {"g01": {"shared": "w1", "mode": "shared"}},
        "cancelled_games": [],
    }

    parsed = parse_tournament_run_state_boundary(payload)
    assert parsed["openbench_state"] is not None
    assert parsed["openbench_state"]["target_test_id"] == 12
    assert parsed["openbench_state"]["submitted"]["losses"] == 2
    assert parsed["completed_game_summaries"]["g01"]["game_result"] == "BLACK_WIN"
    assert parsed["game_instance_overrides"]["g01"]["shared"] == "w1"


def test_parse_tournament_run_state_boundary_rejects_invalid_completed_summary() -> None:
    payload = {
        "completed_game_summaries": {"g01": "bad"},
    }

    with pytest.raises(ContractParseError, match="Failed to parse wire payload"):
        parse_tournament_run_state_boundary(payload)


def test_parse_tournament_run_state_boundary_rejects_legacy_summary_keys() -> None:
    payload = {
        "completed_game_summaries": {
            "g01": {
                "game_result": "BLACK_WIN",
                "result_code": 0,
            }
        }
    }

    with pytest.raises(ContractParseError, match="Failed to parse wire payload"):
        parse_tournament_run_state_boundary(payload)


def test_parse_tournament_run_state_boundary_rejects_legacy_cancelled_assignment_key() -> None:
    payload = {
        "cancelled_games": [
            {
                "game_id": "g01",
                "assigned_instance": "worker-1",
            }
        ]
    }

    with pytest.raises(ContractParseError, match="Failed to parse wire payload"):
        parse_tournament_run_state_boundary(payload)


def test_parse_tournament_run_state_boundary_rejects_invalid_openbench_state() -> None:
    payload = {
        "completed_game_ids": [],
        "openbench_state": {"submitted": {"losses": -1}},
    }

    with pytest.raises(ContractParseError, match="Failed to parse wire payload"):
        parse_tournament_run_state_boundary(payload)


def test_parse_openbench_client_state_boundary_normalizes_values() -> None:
    parsed = parse_openbench_client_state_boundary(
        {
            "submitted": {
                "losses": "2",
                "draws": 1,
                "wins": 0,
            },
            "last_synced_games": "4",
            "target_test_id": "10",
            "claimed_test_id": 0,
            "result_id": None,
            "blacklist": ["0", "5"],
        },
    )
    assert parsed["submitted"]["losses"] == 2
    assert parsed["last_synced_games"] == 4
    assert parsed["target_test_id"] == 10
    assert parsed["claimed_test_id"] is None
    assert parsed["blacklist"] == [0, 5]


def test_parse_openbench_client_state_boundary_rejects_negative_blacklist_value() -> None:
    with pytest.raises(ContractParseError, match="Failed to parse wire payload"):
        parse_openbench_client_state_boundary({"blacklist": [-1]})


def test_openbench_restore_state_raises_contract_error_on_invalid_payload() -> None:
    cfg = OpenBenchClientConfig(
        is_enabled=False,
        mode="existing_test",
        server="https://example.com",
        username="user",
        password="pw",
        target_test_id=1,
        submit_interval_games=1,
        is_strict=True,
        heartbeat_interval_sec=1.0,
        poll_interval_sec=1.0,
        assignment_timeout_sec=1.0,
        is_insecure_http_allowed=True,
        concurrency=1,
    )
    client = OpenBenchClient(cfg, tested_engine="dev", base_engine="base")

    with pytest.raises(ContractParseError, match="Failed to parse wire payload"):
        client.restore_state({"submitted": {"losses": "bad"}})


def test_parse_spsa_run_state_boundary_normalizes_non_negative_fields() -> None:
    payload = {
        "completed_updates": -1,
        "total_updates": "42",
        "is_finished": True,
    }

    parsed = parse_spsa_run_state_boundary(payload, path="run_state.json")
    assert parsed["completed_updates"] == 0
    assert parsed["total_updates"] == 42
    assert parsed["is_finished"] is True


def test_parse_spsa_index_boundary_defaults_to_empty_payload() -> None:
    parsed = parse_spsa_index_boundary({}, path="index.json")
    assert parsed["updates"] == []
    assert parsed["metadata"] == {}


def test_parse_spsa_index_boundary_rejects_non_mapping() -> None:
    with pytest.raises(ContractParseError, match="Failed to parse wire payload"):
        parse_spsa_index_boundary(["bad"], path="index.json")


def test_parse_session_context_snapshot_boundary_accepts_valid_payload() -> None:
    payload = {
        "run_id": "session-001",
        "num_workers": 4,
        "metadata": {
            "runner_type": "tournament",
            "experiment_name": "abc",
            "is_dashboard_enabled": False,
            "scheduler": "round_robin",
            "games_per_pair": 2,
        },
    }

    parsed = parse_session_context_snapshot_boundary(payload, path="session_context.json")
    assert parsed["run_id"] == "session-001"
    assert parsed["num_workers"] == 4
    assert parsed["metadata"]["runner_type"] == "tournament"


def test_parse_session_context_snapshot_boundary_rejects_invalid_run_id() -> None:
    with pytest.raises(ContractParseError, match="Failed to parse wire payload"):
        parse_session_context_snapshot_boundary({"run_id": "", "num_workers": 1}, path="session_context.json")


def test_parse_session_context_snapshot_boundary_rejects_invalid_metadata() -> None:
    with pytest.raises(ContractParseError, match="Failed to parse wire payload"):
        parse_session_context_snapshot_boundary(
            {"run_id": "session-002", "num_workers": 1, "metadata": {"runner_type": "unknown"}},
            path="session_context.json",
        )
