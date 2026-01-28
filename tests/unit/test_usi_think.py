import pytest

from shogiarena.arena.engines.usi_think import UsiThinkRequest, normalize_searchmoves


def test_movetime_command() -> None:
    req = UsiThinkRequest(movetime=500)
    assert req.to_command() == "go movetime 500"


def test_infinite_with_searchmoves() -> None:
    req = UsiThinkRequest(infinite=True, searchmoves=(" 7g7f ", ""))
    assert req.to_command() == "go infinite searchmoves 7g7f"


def test_byoyomi_command_black() -> None:
    req = UsiThinkRequest(
        btime=30000,
        wtime=45000,
        byoyomi=1000,
    )
    assert req.to_command() == "go btime 30000 wtime 45000 byoyomi 1000"


def test_time_increment_requests() -> None:
    req_black = UsiThinkRequest(
        btime=119000,
        wtime=99500,
        binc=1000,
        winc=500,
    )
    assert req_black.to_command() == "go btime 119000 wtime 99500 binc 1000 winc 500"

    req_white = UsiThinkRequest(
        btime=119000,
        wtime=99500,
        binc=1000,
        winc=500,
    )
    assert req_white.to_command() == "go btime 119000 wtime 99500 binc 1000 winc 500"


def test_movetime_requires_positive() -> None:
    with pytest.raises(ValueError):
        UsiThinkRequest(movetime=0)


def test_time_increment_requires_non_negative() -> None:
    with pytest.raises(ValueError):
        UsiThinkRequest(binc=-1)


def test_time_increment_handles_none() -> None:
    req = UsiThinkRequest(
        btime=5000,
        wtime=6000,
    )
    assert req.to_command() == "go btime 5000 wtime 6000"


def test_normalize_searchmoves() -> None:
    assert normalize_searchmoves(["a", "", " b "]) == ("a", "b")
