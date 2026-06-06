from __future__ import annotations

import argparse

import pytest

from shogiarena._core.interfaces.cli.main import CliArgumentError
from shogiarena._core.interfaces.cli.replay_position import (
    _target_request_from_args,
    parse_go_command,
)


def _replay_args(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "searchmoves": None,
        "nodes": None,
        "depth": None,
        "movetime": None,
        "infinite": False,
        "timeout": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_parse_go_command_roundtrips_time_control_request() -> None:
    request = parse_go_command("go btime 100 wtime 200 binc 3 winc 4 byoyomi 500 depth 8 nodes 100")

    assert request.btime == 100
    assert request.wtime == 200
    assert request.binc == 3
    assert request.winc == 4
    assert request.byoyomi == 500
    assert request.depth == 8
    assert request.nodes == 100
    assert request.to_command() == "go btime 100 wtime 200 binc 3 winc 4 byoyomi 500 depth 8 nodes 100"


def test_parse_go_command_parses_searchmoves() -> None:
    request = parse_go_command("go nodes 10 searchmoves 7g7f 2g2f")

    assert request.nodes == 10
    assert tuple(move.to_usi() for move in request.searchmoves) == ("7g7f", "2g2f")


def test_parse_go_command_rejects_unknown_tokens() -> None:
    with pytest.raises(CliArgumentError, match="unsupported go token"):
        parse_go_command("go unsupported 10")


def test_target_request_requires_timeout_for_infinite() -> None:
    args = _replay_args(infinite=True, timeout=None)
    with pytest.raises(CliArgumentError, match="--infinite requires --timeout"):
        _target_request_from_args(args, fallback=None)


def test_target_request_allows_infinite_with_timeout() -> None:
    args = _replay_args(infinite=True, timeout=1.0)
    request = _target_request_from_args(args, fallback=None)
    assert request.is_infinite is True


def test_target_request_rejects_invalid_searchmoves() -> None:
    args = _replay_args(nodes=10, searchmoves=["not-a-move"])
    with pytest.raises(CliArgumentError, match="invalid --searchmoves"):
        _target_request_from_args(args, fallback=None)
