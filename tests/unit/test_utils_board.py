import cshogi
import pytest

from shogiarena.utils.board import normalize_sfen, sfen_parser


@pytest.mark.parametrize(
    "source",
    [
        "startpos",
        "position startpos",
        cshogi.STARTING_SFEN,
        f"sfen {cshogi.STARTING_SFEN}",
    ],
)
def test_sfen_parser_start_position_variants(source: str) -> None:
    board = sfen_parser(source)
    assert board.sfen() == cshogi.STARTING_SFEN


@pytest.mark.parametrize(
    "source",
    [
        "startpos moves 7g7f 3c3d",
        "position startpos moves 7g7f 3c3d",
        f"sfen {cshogi.STARTING_SFEN} moves 7g7f 3c3d",
    ],
)
def test_sfen_parser_applies_moves(source: str) -> None:
    expected = cshogi.Board()
    for move in ("7g7f", "3c3d"):
        expected.push_usi(move)

    board = sfen_parser(source)
    assert board.sfen() == expected.sfen()


def test_sfen_parser_plain_sfen_token() -> None:
    token = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1"
    board = sfen_parser(token)
    assert board.sfen() == token


@pytest.mark.parametrize(
    "source",
    ["", "   ", "startpos 7g7f", "sfen a b c", "startpos moves 7g7f 7g7f"],
)
def test_sfen_parser_invalid_inputs(source: str) -> None:
    with pytest.raises(ValueError):
        sfen_parser(source)


@pytest.mark.parametrize("source", ["startpos", f"sfen {cshogi.STARTING_SFEN}", cshogi.STARTING_SFEN])
def test_normalize_sfen_canonical_startpos(source: str) -> None:
    assert normalize_sfen(source) == "startpos"


def test_normalize_sfen_with_moves_returns_canonical_token() -> None:
    expected = cshogi.Board()
    for move in ("7g7f", "3c3d"):
        expected.push_usi(move)

    canonical = normalize_sfen("startpos moves 7g7f 3c3d")
    assert canonical == expected.sfen()


def test_normalize_sfen_with_position_prefix() -> None:
    expr = f"position sfen {cshogi.STARTING_SFEN} moves 7g7f"
    expected = cshogi.Board()
    expected.push_usi("7g7f")
    assert normalize_sfen(expr) == expected.sfen()


def test_normalize_sfen_plain_token_round_trip() -> None:
    token = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1"
    assert normalize_sfen(token) == "startpos"


def test_normalize_sfen_invalid_raises() -> None:
    with pytest.raises(ValueError):
        normalize_sfen("not a valid sfen")


def test_normalize_sfen_non_startpos_token_kept() -> None:
    board = cshogi.Board()
    board.push_usi("7g7f")
    token = board.sfen()
    assert normalize_sfen(token) == token
