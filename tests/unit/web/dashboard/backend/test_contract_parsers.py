from __future__ import annotations

import pytest

from shogiarena._core.interfaces.boundaries.parsers.snapshot import parse_dashboard_snapshot_payload
from shogiarena._core.interfaces.boundaries.parsers.spsa_stream import parse_spsa_payload
from shogiarena._core.interfaces.boundaries.parsers.tournament import parse_tournament_payload
from shogiarena._core.shared.kernel.exceptions import ContractParseError


def test_parse_tournament_games_list_payload_rejects_negative_offset() -> None:
    payload = {
        "games": [],
        "total": 0,
        "offset": -1,
        "limit": 100,
    }
    with pytest.raises(ContractParseError):
        parse_tournament_payload("games_list", payload, path="tests.tournament.games_list")


def test_parse_tournament_stream_payload_rejects_negative_seq() -> None:
    payload = {
        "stream": "tournament_summary",
        "seq": -1,
        "type": "heartbeat",
        "timestamp": 1730000000,
        "data": {},
    }
    with pytest.raises(ContractParseError):
        parse_tournament_payload("stream", payload, path="tests.tournament.stream")


def test_parse_dashboard_games_snapshot_payload_requires_kind() -> None:
    payload = {
        "rows": [],
        "snapshot_meta": {},
    }
    with pytest.raises(ContractParseError):
        parse_dashboard_snapshot_payload("games", payload, path="tests.snapshot.games_missing_kind")


def test_parse_dashboard_games_snapshot_payload_rejects_non_object_row() -> None:
    payload = {
        "kind": "bulk",
        "revision": 1,
        "base_revision": None,
        "rows": [{"game_id": "g1"}, 42],
        "snapshot_meta": {},
    }
    with pytest.raises(ContractParseError):
        parse_dashboard_snapshot_payload("games", payload, path="tests.snapshot.games_invalid_row")


def test_parse_spsa_stream_payload_requires_type() -> None:
    payload = {
        "stream": "summary",
        "seq": 1,
    }
    with pytest.raises(ContractParseError):
        parse_spsa_payload("stream", payload, path="tests.spsa.stream")


def test_parse_spsa_websocket_payload_rejects_non_list_updates() -> None:
    payload = {
        "type": "update",
        "updates": {"update_idx": 1},
    }
    with pytest.raises(ContractParseError):
        parse_spsa_payload("websocket", payload, path="tests.spsa.websocket")
