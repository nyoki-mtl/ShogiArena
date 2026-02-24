import pytest
from rshogi.core import Board, normalize_usi_position, parse_usi_position
from rshogi.initial_positions import InitialPosition

STARTING_SFEN = InitialPosition.STANDARD.value


@pytest.mark.parametrize(
    "source",
    [
        "startpos",
        "position startpos",
        STARTING_SFEN,
        f"sfen {STARTING_SFEN}",
    ],
)
def test_parse_sfen_start_position_variants(source: str) -> None:
    board = parse_usi_position(source)
    assert board.to_sfen() == STARTING_SFEN


@pytest.mark.parametrize(
    "source",
    [
        "startpos moves 7g7f 3c3d",
        "position startpos moves 7g7f 3c3d",
        f"sfen {STARTING_SFEN} moves 7g7f 3c3d",
    ],
)
def test_parse_sfen_applies_moves(source: str) -> None:
    expected = Board()
    for move in ("7g7f", "3c3d"):
        expected.apply_usi(move)

    board = parse_usi_position(source)
    assert board.to_sfen() == expected.to_sfen()


def test_parse_sfen_plain_sfen_token() -> None:
    token = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1"
    board = parse_usi_position(token)
    assert board.to_sfen() == token


@pytest.mark.parametrize(
    "source",
    ["", "   ", "startpos 7g7f", "sfen a b c", "startpos moves 7g7f 7g7f"],
)
def test_parse_sfen_invalid_inputs(source: str) -> None:
    with pytest.raises(ValueError):
        parse_usi_position(source)


@pytest.mark.parametrize("source", ["startpos", f"sfen {STARTING_SFEN}", STARTING_SFEN])
def test_normalize_sfen_canonical_startpos(source: str) -> None:
    assert normalize_usi_position(source) == "startpos"


def test_normalize_sfen_with_moves_returns_canonical_token() -> None:
    expected = Board()
    for move in ("7g7f", "3c3d"):
        expected.apply_usi(move)

    canonical = normalize_usi_position("startpos moves 7g7f 3c3d")
    assert canonical == expected.to_sfen()


def test_normalize_sfen_with_position_prefix() -> None:
    expr = f"position sfen {STARTING_SFEN} moves 7g7f"
    expected = Board()
    expected.apply_usi("7g7f")
    assert normalize_usi_position(expr) == expected.to_sfen()


def test_normalize_sfen_plain_token_round_trip() -> None:
    token = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1"
    assert normalize_usi_position(token) == "startpos"


def test_normalize_sfen_invalid_raises() -> None:
    with pytest.raises(ValueError):
        normalize_usi_position("not a valid sfen")


def test_normalize_sfen_non_startpos_token_kept() -> None:
    board = Board()
    board.apply_usi("7g7f")
    token = board.to_sfen()
    assert normalize_usi_position(token) == token
