"""Type stubs for cshogi.KIF module - KIF format parser and exporter"""

import cshogi

KIFU_FROM_SQUARE_NAMES: list[str]
KIFU_TO_SQUARE_NAMES: list[str]
PIECE_BOD_SYMBOLS: dict[int, str]

class ParserException(Exception):
    """Exception raised by KIF parser"""

    pass

class Parser(cshogi.Parser):
    """KIF format parser"""

    pass

class Exporter:
    """KIF format exporter"""

    def __init__(self, path: str | None = None) -> None: ...
    def header(self, board: cshogi.Board, black_name: str = "", white_name: str = "") -> None: ...
    def move(self, move: cshogi.Move | None, board: cshogi.Board, time: float | None = None) -> None: ...
    def end(self, result: str) -> None: ...
    def close(self) -> None: ...

def board_to_bod(board: cshogi.Board) -> str: ...
def move_to_bod(move: cshogi.Move, board: cshogi.Board) -> str: ...
def move_to_kif(move: cshogi.Move, prev_move: cshogi.Move | None = None) -> str: ...
def sec_to_time(sec: float) -> str: ...
