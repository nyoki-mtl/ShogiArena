"""Type stubs for cshogi.PGN module - PGN format parser and exporter"""

import cshogi

class Parser(cshogi.Parser):
    """PGN format parser"""

    pass

class Exporter:
    """PGN format exporter"""

    def __init__(self) -> None: ...
    def header(self, board: cshogi.Board) -> str: ...
    def move(self, move: cshogi.Move, board: cshogi.Board) -> str: ...
    def end(self, result: str) -> str: ...

def move_to_san(move: cshogi.Move, board: cshogi.Board) -> str: ...
