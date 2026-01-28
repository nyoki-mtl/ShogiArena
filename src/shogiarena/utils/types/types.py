from enum import IntEnum

# 勝敗/引き分け判定に再利用するセットをモジュールレベルに定義
_BLACK_WIN_RESULTS = frozenset(
    {
        0,  # BLACK_WIN
        4,  # BLACK_WIN_BY_DECLARATION
        8,  # BLACK_WIN_BY_FORFEIT
        12,  # BLACK_WIN_BY_ILLEGAL_MOVE
        16,  # BLACK_WIN_BY_TIMEOUT
    }
)
_WHITE_WIN_RESULTS = frozenset(
    {
        1,  # WHITE_WIN
        5,  # WHITE_WIN_BY_DECLARATION
        9,  # WHITE_WIN_BY_FORFEIT
        13,  # WHITE_WIN_BY_ILLEGAL_MOVE
        17,  # WHITE_WIN_BY_TIMEOUT
    }
)
_DRAW_RESULTS = frozenset({2, 6})
_DECLARATION_RESULTS = frozenset({4, 5})

_KIF_RESULT_LABELS = {
    0: "投了",
    1: "投了",
    2: "千日手",
    3: "反則負け",
    4: "宣言勝ち",
    5: "宣言勝ち",
    6: "持将棋",
    7: "中断",
    8: "反則負け",
    9: "反則負け",
    10: "中断",
    12: "反則負け",
    13: "反則負け",
    16: "切れ負け",
    17: "切れ負け",
}

_CSA_TO_RESULT = {
    ("%TORYO", 1): 0,
    ("%TORYO", 2): 1,
    ("%SENNICHITE", 0): 2,
    ("%ERROR", 0): 3,
    ("%ERROR", 1): 3,
    ("%ERROR", 2): 3,
    ("%ILLEGAL_MOVE", 0): 3,
    ("%ILLEGAL_MOVE", 1): 12,
    ("%ILLEGAL_MOVE", 2): 13,
    ("%KACHI", 1): 4,
    ("%KACHI", 2): 5,
    ("%JISHOGI", 0): 6,
    ("%CHUDAN", 0): 10,
    ("%TIME_UP", 1): 16,
    ("%TIME_UP", 2): 17,
}


# 手番を表現するEnum
class Turn(IntEnum):
    BLACK = 0  # 先手
    WHITE = 1  # 後手

    # 反転させた手番を返す
    def flip(self) -> "Turn":
        return Turn.WHITE if self is Turn.BLACK else Turn.BLACK


class GameResult(IntEnum):
    # 0b11でandをとったときに00が先手勝ち、01が後手勝ち、10が引き分け、11がエラーに
    BLACK_WIN = 0  # 先手勝ち
    WHITE_WIN = 1  # 後手勝ち
    DRAW_BY_REPETITION = 2  # 千日手による引き分け
    ERROR = 3  # エラー
    BLACK_WIN_BY_DECLARATION = 4  # 先手宣言勝ち
    WHITE_WIN_BY_DECLARATION = 5  # 後手宣言勝ち
    DRAW_BY_MAX_PLIES = 6  # 最大手数（半手）または持将棋による引き分け
    INVALID = 7  # 無効対局
    BLACK_WIN_BY_FORFEIT = 8  # 先手不戦勝
    WHITE_WIN_BY_FORFEIT = 9  # 後手不戦勝
    PAUSED = 10  # 中断
    BLACK_WIN_BY_ILLEGAL_MOVE = 12  # 先手反則勝ち
    WHITE_WIN_BY_ILLEGAL_MOVE = 13  # 後手反則勝ち
    BLACK_WIN_BY_TIMEOUT = 16  # 先手時間切れ勝ち
    WHITE_WIN_BY_TIMEOUT = 17  # 後手時間切れ勝ち

    # ゲームは先手勝ちか？
    def is_black_win(self) -> bool:
        return self.value in _BLACK_WIN_RESULTS

    # ゲームは後手勝ちか？
    def is_white_win(self) -> bool:
        return self.value in _WHITE_WIN_RESULTS

    # ゲームは引き分けであるのか？
    def is_draw(self) -> bool:
        return self.value in _DRAW_RESULTS

    # どちらかが勝利したか？
    def is_win(self) -> bool:
        return self.is_black_win() or self.is_white_win()

    # 宣言勝ちか？
    def is_win_by_declaration(self) -> bool:
        return self.value in _DECLARATION_RESULTS

    # 引き分け以外のGameResultをTurnに変換する
    def to_turn(self) -> Turn:
        # 2bitで2, 3はそれぞれ引き分け、エラー
        turn_int = self & 3
        if turn_int not in (0, 1):
            raise ValueError(f"GameResult {self.name} cannot be mapped to a winning turn")
        return Turn(turn_int)

    # 先手から見て、勝ちを1、負けを0、引き分けを0.5として返す。中断局などは-1
    def to_black_result(self) -> int | float:
        if self.is_black_win():
            return 1
        if self.is_white_win():
            return 0
        if self.is_draw():
            return 0.5
        return -1

    # 後手から見て、勝ちを1、負けを0、引き分けを0.5として返す。中断局などは-1
    def to_white_result(self) -> int | float:
        if self.is_black_win():
            return 0
        if self.is_white_win():
            return 1
        if self.is_draw():
            return 0.5
        return -1

    # kif形式の文字列へ
    def to_kif_str(self) -> str:
        try:
            return _KIF_RESULT_LABELS[self.value]
        except KeyError as exc:  # pragma: no cover - defensive
            raise ValueError(f"No KIF label defined for result {self.name}") from exc

    @classmethod
    def win_from_turn(cls, win_turn: Turn) -> "GameResult":
        return cls.BLACK_WIN if win_turn == Turn.BLACK else cls.WHITE_WIN

    @classmethod
    def win_by_declaration_from_turn(cls, win_turn: Turn) -> "GameResult":
        return cls.BLACK_WIN_BY_DECLARATION if win_turn == Turn.BLACK else cls.WHITE_WIN_BY_DECLARATION

    @classmethod
    def win_by_forfeit_from_turn(cls, win_turn: Turn) -> "GameResult":
        return cls.BLACK_WIN_BY_FORFEIT if win_turn == Turn.BLACK else cls.WHITE_WIN_BY_FORFEIT

    @classmethod
    def win_by_illegal_move_from_turn(cls, win_turn: Turn) -> "GameResult":
        return cls.BLACK_WIN_BY_ILLEGAL_MOVE if win_turn == Turn.BLACK else cls.WHITE_WIN_BY_ILLEGAL_MOVE

    @classmethod
    def win_by_timeout_from_turn(cls, win_turn: Turn) -> "GameResult":
        return cls.BLACK_WIN_BY_TIMEOUT if win_turn == Turn.BLACK else cls.WHITE_WIN_BY_TIMEOUT

    @classmethod
    def from_csa(cls, csa_format: tuple[str, int]) -> "GameResult":
        try:
            value = _CSA_TO_RESULT[csa_format]
        except KeyError as exc:
            raise ValueError(f"Unsupported CSA result tuple: {csa_format}") from exc
        return cls(value)
