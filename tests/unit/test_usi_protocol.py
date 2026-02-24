import pytest
from rshogi.core import Move

from shogiarena.arena.engines.usi_protocol import (
    UsiIdField,
    UsiOption,
    UsiProtocolParser,
)
from shogiarena.arena.engines.usi_types import UsiThinkPV


def test_parse_id_line() -> None:
    parsed = UsiProtocolParser.parse_id("id name TestEngine")
    assert parsed == UsiIdField(key="name", value="TestEngine")


def test_parse_id_invalid() -> None:
    with pytest.raises(ValueError):
        UsiProtocolParser.parse_id("id name")


def test_parse_option_spin() -> None:
    line = "option name Threads type spin default 1 min 1 max 32"
    option = UsiProtocolParser.parse_option(line)
    assert option == UsiOption(
        name="Threads",
        option_type="spin",
        default="1",
        current="1",
        minimum=1,
        maximum=32,
        choices=(),
    )


def test_parse_option_combo_multiple_vars() -> None:
    line = "option name Style type combo default Normal var Solid var Normal var Risky"
    option = UsiProtocolParser.parse_option(line)
    assert option == UsiOption(
        name="Style",
        option_type="combo",
        default="Normal",
        current="Normal",
        minimum=None,
        maximum=None,
        choices=("Solid", "Normal", "Risky"),
    )


def test_parse_option_unrelated_line() -> None:
    assert UsiProtocolParser.parse_option("usiok") is None


def test_parse_info_line() -> None:
    pv = UsiProtocolParser.parse_info("info depth 10 nodes 123 time 45")
    assert isinstance(pv, UsiThinkPV)
    assert pv.depth == 10
    assert pv.nodes == 123
    assert pv.time == 45


def test_parse_info_unrelated_line() -> None:
    assert UsiProtocolParser.parse_info("bestmove 7g7f") is None


def test_parse_bestmove_with_pv() -> None:
    pv = UsiProtocolParser.parse_info("info depth 12 score cp 45 pv 7g7f")
    result = UsiProtocolParser.parse_bestmove("bestmove 7g7f ponder 3c3d", pvs=[pv] if pv else None)
    assert result is not None
    assert result.bestmove == Move.from_usi("7g7f")
    assert result.ponder == Move.from_usi("3c3d")
    assert result.pvs and result.pvs[0].depth == 12


def test_parse_bestmove_unrelated_line() -> None:
    assert UsiProtocolParser.parse_bestmove("info depth 12") is None
