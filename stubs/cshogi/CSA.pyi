"""Type stubs for cshogi.CSA module - CSA format parser and exporter"""

import cshogi

COLOR_SYMBOLS: dict[int, str]
JAPANESE_END_GAMES: dict[str, str]

class Parser(cshogi.Parser):
    """CSA format parser"""

    pass

class Exporter:
    """CSA format exporter"""

    def __init__(self) -> None: ...
    def header(self, board: cshogi.Board) -> str: ...
    def move(self, move: cshogi.Move, board: cshogi.Board) -> str: ...
    def end(self, result: str) -> str: ...
