from __future__ import annotations

import pytest

from shogiarena._core.interfaces.cli.main import CliArgumentError
from shogiarena._core.interfaces.cli.run.analyze import parse_position_argument


def test_parse_position_argument_validates_moves_with_board_state() -> None:
    position, moves = parse_position_argument("position startpos moves 7g7f 3c3d")

    assert position == "startpos"
    assert tuple(move.to_usi() for move in moves) == ("7g7f", "3c3d")


def test_parse_position_argument_rejects_illegal_move() -> None:
    with pytest.raises(CliArgumentError, match="invalid or illegal move"):
        parse_position_argument("position startpos moves 7g7e")
