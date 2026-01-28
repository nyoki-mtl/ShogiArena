"""Type stubs for cshogi.KI2 module - KI2 format parser and exporter"""

import cshogi

class Parser(cshogi.Parser):
    """KI2 format parser"""

    pass

class Exporter:
    """KI2 format exporter"""

    def __init__(self) -> None: ...
    def header(self, board: cshogi.Board) -> str: ...
    def move(self, move: cshogi.Move, board: cshogi.Board) -> str: ...
    def end(self, result: str) -> str: ...

def move_to_ki2(move: cshogi.Move, board: cshogi.Board) -> str: ...
