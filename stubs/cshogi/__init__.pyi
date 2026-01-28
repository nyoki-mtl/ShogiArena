"""Type stubs for cshogi - High-performance shogi library

This file provides type hints for the cshogi library.
Repository: https://github.com/TadaoYamaoka/cshogi
"""

from collections.abc import Iterator
from typing import Any, overload

import numpy as np
from numpy.typing import NDArray

# Color constants
BLACK: int
WHITE: int
COLOR_NONE: int

# Piece type constants
PAWN: int
LANCE: int
KNIGHT: int
SILVER: int
GOLD: int
BISHOP: int
ROOK: int
KING: int
PROM_PAWN: int
PROM_LANCE: int
PROM_KNIGHT: int
PROM_SILVER: int
PROM_BISHOP: int
PROM_ROOK: int

# Piece constants (color + piece type)
BPAWN: int
BLANCE: int
BKNIGHT: int
BSILVER: int
BGOLD: int
BBISHOP: int
BROOK: int
BKING: int
BPROM_PAWN: int
BPROM_LANCE: int
BPROM_KNIGHT: int
BPROM_SILVER: int
BPROM_BISHOP: int
BPROM_ROOK: int
WPAWN: int
WLANCE: int
WKNIGHT: int
WSILVER: int
WGOLD: int
WBISHOP: int
WROOK: int
WKING: int
WPROM_PAWN: int
WPROM_LANCE: int
WPROM_KNIGHT: int
WPROM_SILVER: int
WPROM_BISHOP: int
WPROM_ROOK: int
NONE: int

# Square constants (0-80)
A1: int
A2: int
A3: int
A4: int
A5: int
A6: int
A7: int
A8: int
A9: int
B1: int
B2: int
B3: int
B4: int
B5: int
B6: int
B7: int
B8: int
B9: int
C1: int
C2: int
C3: int
C4: int
C5: int
C6: int
C7: int
C8: int
C9: int
D1: int
D2: int
D3: int
D4: int
D5: int
D6: int
D7: int
D8: int
D9: int
E1: int
E2: int
E3: int
E4: int
E5: int
E6: int
E7: int
E8: int
E9: int
F1: int
F2: int
F3: int
F4: int
F5: int
F6: int
F7: int
F8: int
F9: int
G1: int
G2: int
G3: int
G4: int
G5: int
G6: int
G7: int
G8: int
G9: int
H1: int
H2: int
H3: int
H4: int
H5: int
H6: int
H7: int
H8: int
H9: int
I1: int
I2: int
I3: int
I4: int
I5: int
I6: int
I7: int
I8: int
I9: int

# Special squares
SQUARES_L90: list[int]
SQUARES_R45: list[int]

# Starting position
STARTING_SFEN: str

# Symbols
PIECE_SYMBOLS: str
PIECE_JAPANESE_SYMBOLS: list[str]
HAND_PIECE_JAPANESE_SYMBOLS: list[str]
NUMBER_JAPANESE_NUMBER_SYMBOLS: list[str]
NUMBER_JAPANESE_KANJI_SYMBOLS: list[str]
SQUARE_NAMES: list[str]
SQUARES_L45: list[int]

# Move flags
MOVE_NONE: int
MOVE_DROP: int
MOVE_PROMOTE: int

# Game result constants
BLACK_WIN: int
WHITE_WIN: int
DRAW: int
BLACK_DRAW: int
WHITE_DRAW: int
NOT_REPETITION: int
REPETITION: int
REPETITION_WIN: int
REPETITION_LOSE: int
REPETITION_DRAW: int
REPETITION_SUPERIOR: int
REPETITION_INFERIOR: int

# Hand piece constants
HPAWN: int
HLANCE: int
HKNIGHT: int
HSILVER: int
HGOLD: int
HBISHOP: int
HROOK: int

# Other constants
MAX_PIECES_IN_HAND: list[int]
PIECES_IN_HAND_SUM: int
MAX_MOVES_LEN: int
MOVE16_MAX: int
MOVE16_NONE: int
NOTUSE: int

# Sequences and ranges
COLORS: range
HAND_PIECES: range
PIECE_TYPES: list[int]
PIECE_TYPES_WITH_NONE: range
GAME_RESULTS: range
REPETITION_TYPES: range
HAND_PIECE_SYMBOLS: str
PIECES: list[int]
SQUARES: list[int]

# SVG constants
SVG_COORDINATES: dict[int, tuple[float, float]]
SVG_PIECE_DEFS: dict[int, str]
SVG_PIECE_DEF_IDS: dict[int, str]
SVG_SQUARES: list[str]

# NumPy dtype constants
dtypeEval: type[np.int16]
dtypeGameResult: type[np.int8]
dtypeHcp: tuple[str, tuple[int, ...]]
dtypeKey: type[np.uint64]
dtypeMove16: type[np.int16]

# Type aliases
Move = int
Square = int
Piece = int
Color = int
GameResult = int

# Functions
def piece_to_piece_type(piece: Piece) -> int: ...
def move_to(move: Move) -> Square: ...
def move_from(move: Move) -> Square: ...
def move_cap(move: Move) -> Piece: ...
def move_is_drop(move: Move) -> bool: ...
def move_is_promotion(move: Move) -> bool: ...
def move_drop_hand_piece(move: Move) -> int: ...
def move16(move: Move) -> int: ...
def move16_from_psv(move16: int) -> Move: ...
def move16_to_psv(move16: int) -> int: ...
def move_from_usi(usi_move: str) -> Move: ...
def move_from_csa(csa_move: str) -> Move: ...
def move_to_usi(move: Move) -> str: ...
def move_to_csa(move: Move) -> str: ...
def move_rotate(move: Move) -> Move: ...
def make_file(file: int) -> int: ...
def make_rank(rank: int) -> int: ...
def file_of(sq: Square) -> int: ...
def rank_of(sq: Square) -> int: ...
def move_from_piece_type(piece_type: int) -> Piece: ...
def zobrist_hash(piece: Piece, sq: Square) -> int: ...
def zobrist_hash_hand(color: Color, piece_type: int, num: int) -> int: ...
def hand_piece_to_piece_type(hand_piece: int) -> int: ...
def opponent(color: Color) -> Color: ...
def rotate_sfen(sfen: str) -> str: ...
def to_usi(move: Move) -> str: ...
def to_csa(move: Move) -> str: ...

# Classes
class Board:
    def __init__(self, sfen: str | None = None) -> None: ...

    # Properties
    @property
    def turn(self) -> Color: ...
    @property
    def move_number(self) -> int: ...
    @property
    def legal_moves(self) -> list[Move]: ...
    @property
    def pieces_in_hand(self) -> list[list[int]]: ...  # [color][piece_type]
    @property
    def pieces(self) -> list[Piece]: ...
    def piece_planes(self, features: NDArray[np.uint8]) -> None: ...
    def king_square(self, c: int) -> Square: ...
    def is_check(self) -> bool: ...
    def is_game_over(self) -> bool: ...
    def is_nyugyoku(self) -> bool: ...
    def is_draw(self, ply: int | None = None) -> int: ...
    def is_mate(self, ply: int) -> bool: ...
    def mate_move(self, ply: int) -> int: ...
    def mate_move_in_1ply(self) -> int: ...
    @property
    def ply(self) -> int: ...
    def sfen(self) -> str: ...
    def book_key(self) -> int: ...
    def zobrist_hash(self) -> int: ...
    @property
    def history(self) -> list[Move]: ...

    # Methods
    def is_ok(self) -> bool: ...
    def reset(self) -> None: ...
    def set_sfen(self, sfen: str) -> None: ...
    def set_position(self, position: str) -> None: ...
    def set_hcp(self, hcp: bytes | NDArray[np.uint8]) -> None: ...
    def set_psfen(self, psfen: bytes | NDArray[np.uint8]) -> None: ...
    def piece(self, sq: Square) -> Piece: ...
    def piece_type(self, sq: Square) -> int: ...
    def move(self, from_square: Square, to_square: Square, promotion: bool = False) -> Move: ...
    def drop_move(self, to_square: Square, drop_piece_type: int) -> Move: ...
    def move_from_usi(self, usi_move: str) -> Move: ...
    def move_from_csa(self, csa_move: str) -> Move: ...
    def move_from_move16(self, move16: int) -> Move: ...
    def push(self, move: Move) -> bool: ...
    def push_usi(self, usi_move: str) -> bool: ...
    def push_csa(self, csa_move: str) -> bool: ...
    def push_move16(self, move16: int) -> bool: ...
    def push_pass(self) -> bool: ...
    def pop(self) -> Move: ...
    def pop_pass(self) -> bool: ...
    def peek(self) -> Move: ...
    def is_legal(self, move: Move) -> bool: ...
    def is_pseudo_legal(self, move: Move) -> bool: ...
    def is_attacked_by(self, color: Color, sq: Square) -> bool: ...
    def attackers_to(self, color: Color, sq: Square) -> int: ...
    def to_hcp(self, hcp: NDArray[np.uint8]) -> None: ...
    def to_psfen(self, psfen: NDArray[np.uint8]) -> None: ...
    def to_svg(self, lastmove: Move | None = None) -> str: ...
    def to_bod(self) -> str: ...
    def csa_pos(self) -> str: ...
    def copy(self, stack: bool = True) -> Board: ...
    def __copy__(self) -> Board: ...
    def __deepcopy__(self, memo: dict[str, object]) -> Board: ...
    def __str__(self) -> str: ...
    def __repr__(self) -> str: ...

    # DL Shogi methods
    def _dlshogi_make_input_features(self, features1: NDArray[np.float32], features2: NDArray[np.float32]) -> None:
        """Internal method to extract dlshogi input features from the board.

        This is called internally by cshogi.dlshogi.make_input_features().

        Args:
            features1: A numpy array of shape (FEATURES1_NUM, 9, 9) to be filled
                      with the first set of features (board position features)
            features2: A numpy array of shape (FEATURES2_NUM, 9, 9) to be filled
                      with the second set of features (auxiliary features)
        """
        ...

class LegalMoveList:
    def __init__(self) -> None: ...
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[Move]: ...
    def __getitem__(self, index: int) -> Move: ...
    def append(self, move: Move) -> None: ...
    def extend(self, moves: list[Move]) -> None: ...

class PseudoLegalMoveList:
    def __init__(self) -> None: ...
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[Move]: ...
    def __getitem__(self, index: int) -> Move: ...

class HuffmanCodedPos:
    @staticmethod
    def encode(board: Board) -> bytes: ...
    @staticmethod
    def decode(data: bytes) -> Board: ...

class HuffmanCodedPosAndEval:
    hcp: bytes
    eval: int
    bestMove16: int
    gameResult: GameResult

    def __init__(self) -> None: ...
    @overload
    def decode(self) -> Board: ...
    @overload
    def decode(self, board: Board) -> None: ...

class PackedSfenValue:
    sfen: bytes
    score: int
    move: int
    gamePly: int
    game_result: GameResult

    def __init__(self) -> None: ...

class BookEntry:
    key: int
    move: Move
    count: int
    score: int

    def __init__(self) -> None: ...

# DfPn solver class
class DfPn:
    """Depth-first proof number search solver"""

    def __init__(self) -> None: ...
    def set_max_search_node(self, max_node: int) -> None: ...
    def set_max_depth(self, max_depth: int) -> None: ...
    def set_draw_ply(self, draw_ply: int) -> None: ...
    def search(self, board: Board, moves: list[Move]) -> bool: ...
    def search_andnode(self, board: Board, moves: list[Move]) -> bool: ...
    def stop(self) -> None: ...
    def get_move(self) -> Move: ...
    def get_pv(self) -> list[Move]: ...
    @property
    def searched_node(self) -> int: ...

# SVG wrapper class
class SvgWrapper(str):
    """String subclass for SVG representation"""

    pass

# NumPy structured array type for packed SFEN
PackedSfen: type[np.dtype[Any]]

# Notation parsing functions
class Parser:
    def parse_file(self, path: str) -> list[dict[str, object]]: ...
    def parse_str(self, string: str) -> list[dict[str, object]]: ...
    @property
    def comment(self) -> list[str]: ...
    @property
    def comments(self) -> list[list[str]]: ...
    @property
    def endgame(self) -> str: ...
    @property
    def moves(self) -> list[int]: ...
    @property
    def names(self) -> list[str]: ...
    @property
    def ratings(self) -> list[int]: ...
    @property
    def scores(self) -> list[int]: ...
    def sfen(self) -> str: ...
    @property
    def times(self) -> list[float]: ...
    @property
    def var_info(self) -> list[object]: ...
    @property
    def version(self) -> str: ...
    @property
    def win(self) -> int: ...

    # CSA-specific methods
    def parse_csa_file(self, path: str) -> list[dict[str, object]]: ...
    def parse_csa_str(self, string: str) -> list[dict[str, object]]: ...

# DNN evaluation functions
class Evaluator:
    def __init__(self, model_path: str) -> None: ...
    def eval(self, board: Board) -> float: ...
    def eval_with_move(self, board: Board) -> tuple[float, Move]: ...

# Elo rating calculation
def calculate_elo(wins: int, losses: int, draws: int) -> float: ...
def calculate_elo_difference(wins: int, losses: int, draws: int) -> float: ...
