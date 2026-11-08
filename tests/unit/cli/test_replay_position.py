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


def test_load_replay_target_and_trace_match_preserve_opening_moves_from_schedule_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    from pathlib import Path

    import rsshogi.record
    from rsshogi.core import Move

    from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkRequest
    from shogiarena._core.interfaces.cli import replay_position
    from shogiarena._core.interfaces.cli.replay_position import (
        SearchTrace,
        _find_trace_for_ply,
        _load_replay_target,
    )
    from shogiarena._core.shared.kernel.game_results import GameResult
    from shogiarena._core.shared.kernel.schedule_metadata import attach_schedule_metadata

    run_dir = Path(str(tmp_path))
    post_opening_sfen = "lnsgkgsnl/1r5b1/pppppp1pp/6p2/9/2P6/PP1PPPPPP/1B5R1/LNSGKGSNL b - 3"
    record = rsshogi.record.Record.from_usi_main_line(
        post_opening_sfen,
        ["2g2f", "8c8d"],
        result=GameResult.BLACK_WIN,
    )
    attach_schedule_metadata(
        game_record=record,
        schedule_metadata={
            "schema_version": 1,
            "round_num": 0,
            "initial_sfen": post_opening_sfen,
            "opening_base_sfen": "startpos",
            "opening_moves_usi": ["7g7f", "3c3d"],
        },
    )
    monkeypatch.setattr(replay_position, "_load_game_record", lambda **_kwargs: record)

    target = _load_replay_target(run_dir=run_dir, game_id="g0001-test", ply=2)

    assert target.sfen == "startpos"
    assert tuple(m.to_usi() for m in target.moves_before) == ("7g7f", "3c3d", "2g2f")
    assert target.position_command == "position startpos moves 7g7f 3c3d 2g2f"
    assert target.role == "white"

    traces = [
        SearchTrace(
            position_command="position startpos moves 7g7f 3c3d 2g2f",
            go_command="go nodes 1000",
            sfen="startpos",
            moves=(Move.from_usi("7g7f"), Move.from_usi("3c3d"), Move.from_usi("2g2f")),
            request=UsiThinkRequest(nodes=1000),
        )
    ]
    matched = _find_trace_for_ply(traces, target.ply, expected_moves_before=len(target.moves_before))
    assert matched is traces[0]
