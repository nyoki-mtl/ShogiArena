"""
USI protocol data structures for shogiarena.

Defines compact, typed helpers for common USI messages:
- Evaluation values (centipawn and mate scores)
- Score bounds (upper/lower)
- Thinking info lines (``info ...``)
- Bestmove results (``bestmove ...``)

Notes
- Avoids broad try/except; parsing raises only specific errors where needed.
- Keeps interfaces minimal and explicit; removes redundant aliases.
"""

from enum import Enum

from rshogi.core import Move

_SPECIAL_MOVE_MAP: dict[str, Move] = {
    "resign": Move.MOVE_RESIGN,
    "win": Move.MOVE_WIN,
    "0000": Move.MOVE_NONE,
}


def move_from_usi(usi: str) -> Move:
    """USI手文字列を ``Move`` に変換する。

    ``Move.from_usi`` が直接扱えない特殊手 (resign, win) にも対応する。
    """
    special = _SPECIAL_MOVE_MAP.get(usi)
    if special is not None:
        return special
    return Move.from_usi(usi)


MAX_PLY = 246
VALUE_MATE = 32000
VALUE_MATE_IN_MAX_PLY = VALUE_MATE - MAX_PLY


def _mate_in(ply: int) -> int:
    return VALUE_MATE - ply


def _mated_in(ply: int) -> int:
    return -VALUE_MATE + ply


class UsiEvalValue(int):
    """
    Represents a USI evaluation value, extending int for specific behaviors.
    Handles 'cp' (centipawn) and 'mate' scores.
    """

    def is_mate_score(self) -> bool:
        """Checks if the score represents a checkmate score."""
        return VALUE_MATE_IN_MAX_PLY <= int(self) <= VALUE_MATE

    def is_mated_score(self) -> bool:
        """Checks if the score represents being checkmated."""
        value = int(self)
        mated_min = -VALUE_MATE  # most negative bound
        mated_max = -VALUE_MATE_IN_MAX_PLY
        return mated_min <= value <= mated_max

    def to_string(self) -> str:
        """Return the score in USI "score" field format.

        Examples:
        - "cp 17"
        - "mate 3"
        - "mate -2"
        """
        if self.is_mate_score():
            mate_ply: int = VALUE_MATE - int(self)
            return f"mate {mate_ply}"
        elif self.is_mated_score():
            mated_ply: int = int(self) - (-VALUE_MATE)
            return f"mate -{abs(mated_ply)}"
        else:
            return f"cp {int(self)}"

    @staticmethod
    def mate_in_ply(ply: int) -> "UsiEvalValue":
        """Creates a UsiEvalValue representing mate in 'ply' moves."""
        if ply < 0:
            raise ValueError("Mate ply must be non-negative")
        score = _mate_in(ply)
        if score < VALUE_MATE_IN_MAX_PLY:
            score = VALUE_MATE_IN_MAX_PLY
        return UsiEvalValue(score)

    @staticmethod
    def mated_in_ply(ply: int) -> "UsiEvalValue":
        """Creates a UsiEvalValue representing being mated in 'ply' moves."""
        if ply < 0:
            raise ValueError("Mated ply must be non-negative")
        score = _mated_in(ply)
        mated_max = -VALUE_MATE_IN_MAX_PLY
        if score > mated_max:
            score = mated_max
        return UsiEvalValue(score)


class UsiBound(Enum):
    """Bound type for an evaluation score.

    Notes:
    - USI (like UCI) commonly uses only ``lowerbound`` and ``upperbound`` tokens.
    - ``EXACT`` is provided for completeness to represent an exact score
      (i.e., when no bound token is present). It does not serialize to a token.
    """

    NONE = 0
    UPPER = 1  # Fail high - score is an upper bound
    LOWER = 2  # Fail low - score is a lower bound
    EXACT = 3  # Score is exact (no bound token in USI)

    def to_string(self) -> str:
        """Return the USI token for this bound or empty for NONE/EXACT."""
        if self == UsiBound.UPPER:
            return "upperbound"
        elif self == UsiBound.LOWER:
            return "lowerbound"
        return ""

    @classmethod
    def from_string(cls, s: str) -> "UsiBound":
        """Parse a USI bound token into ``UsiBound``.

        Accepts non-standard ``exact`` by mapping to ``EXACT``.
        """
        s_lower = s.lower()
        if s_lower == "upperbound":
            return cls.UPPER
        elif s_lower == "lowerbound":
            return cls.LOWER
        elif s_lower == "exact":
            return cls.EXACT
        else:
            return cls.NONE


class UsiThinkPV:
    """
    Parsed representation of a single USI ``info ...`` line.

    Stores evaluation, bound, search stats, and optional PV sequence, if present.
    Attributes are ``None`` when not provided by the engine.
    """

    def __init__(self) -> None:
        self.pv: list[Move] | None = None  # PV moves
        self.eval: UsiEvalValue | None = None  # Evaluation score
        self.bound: UsiBound = UsiBound.NONE  # Evaluation bound (upper, lower)
        self.depth: int | None = None  # Search depth
        self.seldepth: int | None = None  # Selective search depth
        self.nodes: int | None = None  # Nodes searched
        self.time: int | None = None  # Time elapsed in ms since 'go'
        self.hashfull: int | None = None  # Hash usage permill (0-1000)
        self.nps: int | None = None  # Nodes per second
        self.multipv: int | None = None  # MultiPV line number (1-based)
        self.string: str | None = None  # Custom info string (info string ...)

    def __str__(self) -> str:
        return self.to_debug_string()

    def __repr__(self) -> str:
        return self.to_debug_string()

    def to_debug_string(self) -> str:
        """Format a compact string for debugging/logging."""
        parts: list[str] = []
        if self.multipv is not None:
            parts.append(f"MPV:{self.multipv}")
        if self.depth is not None:
            parts.append(f"D:{self.depth}")
        if self.seldepth is not None:
            parts.append(f"SD:{self.seldepth}")
        if self.eval is not None:
            parts.append(f"Eval:{self.eval.to_string()}{self.bound.to_string()[:1]}")
        if self.time is not None:
            parts.append(f"T:{self.time}ms")
        if self.nodes is not None:
            parts.append(f"N:{self.nodes}")
        if self.pv:
            parts.append(f"PV:{' '.join(m.to_usi() for m in self.pv)}")
        if self.string:
            parts.append(f"Str:{self.string}")
        return f"UsiThinkPV({', '.join(parts)})"

    def to_info_string(self) -> str:
        """Format a standard USI ``info ...`` string from fields."""
        parts: list[str] = []
        if self.multipv is not None:
            parts.extend(["multipv", str(self.multipv)])
        if self.depth is not None:
            parts.extend(["depth", str(self.depth)])
        if self.seldepth is not None:
            parts.extend(["seldepth", str(self.seldepth)])
        if self.time is not None:
            parts.extend(["time", str(self.time)])
        if self.nodes is not None:
            parts.extend(["nodes", str(self.nodes)])
        if self.nps is not None:
            parts.extend(["nps", str(self.nps)])
        if self.hashfull is not None:
            parts.extend(["hashfull", str(self.hashfull)])

        if self.eval is not None:
            eval_str = self.eval.to_string()
            parts.extend(["score", *eval_str.split()])
            bound_str = self.bound.to_string()
            if bound_str:
                parts.append(bound_str)

        if self.string is not None:
            parts.extend(["string", self.string])

        if self.pv is not None:
            parts.extend(["pv", *(m.to_usi() for m in self.pv)])

        if not parts:
            return ""

        return "info " + " ".join(parts)

    @classmethod
    def from_info_string(cls, info_line: str) -> "UsiThinkPV | None":
        """Parse a USI ``info ...`` line.

        Returns ``UsiThinkPV`` when the line contains any recognized metrics
        (depth, seldepth, nodes, time, nps, hashfull, multipv, pv) or score/string.
        Returns ``None`` only if the line does not start with ``info`` or
        contains no recognized tokens.
        """
        if not info_line.startswith("info "):
            return None

        args = info_line.split()[1:]  # Remove "info " prefix
        pv_info = cls()
        current_eval: UsiEvalValue | None = None
        current_bound: UsiBound = UsiBound.NONE
        i = 0

        while i < len(args):
            token = args[i]
            i += 1

            if token == "depth" and i < len(args):
                pv_info.depth = int(args[i])
                i += 1
            elif token == "seldepth" and i < len(args):
                pv_info.seldepth = int(args[i])
                i += 1
            elif token == "time" and i < len(args):
                pv_info.time = int(args[i])
                i += 1
            elif token == "nodes" and i < len(args):
                pv_info.nodes = int(args[i])
                i += 1
            elif token == "pv":
                pv_moves: list[Move] = []
                for raw_move in args[i:]:
                    try:
                        pv_moves.append(move_from_usi(raw_move))
                    except ValueError:
                        break
                pv_info.pv = pv_moves or None
                break  # pv is the last element
            elif token == "multipv" and i < len(args):
                pv_info.multipv = int(args[i])
                i += 1
            elif token == "score" and i < len(args):
                score_type = args[i]
                i += 1
                if score_type == "cp" and i < len(args):
                    cp_val = int(args[i])
                    current_eval = UsiEvalValue(cp_val)
                    i += 1
                elif score_type == "mate" and i < len(args):
                    mate_val = args[i]
                    i += 1
                    if mate_val == "+":
                        current_eval = UsiEvalValue.mate_in_ply(0)
                    elif mate_val == "-":
                        current_eval = UsiEvalValue.mated_in_ply(0)
                    else:
                        ply = int(mate_val)
                        current_eval = (
                            UsiEvalValue.mate_in_ply(ply) if ply >= 0 else UsiEvalValue.mated_in_ply(abs(ply))
                        )
            elif token == "hashfull" and i < len(args):
                pv_info.hashfull = int(args[i])
                i += 1
            elif token == "nps" and i < len(args):
                pv_info.nps = int(args[i])
                i += 1
            elif token == "string":
                pv_info.string = " ".join(args[i:])
                break
            elif token == "lowerbound":
                current_bound = UsiBound.LOWER
            elif token == "upperbound":
                current_bound = UsiBound.UPPER

        # Finalize parsed evaluation/bound if present
        if current_eval is not None:
            pv_info.eval = current_eval
            pv_info.bound = current_bound

        # Return an object when any recognized info exists
        has_any = (
            (pv_info.eval is not None)
            or (pv_info.depth is not None)
            or (pv_info.seldepth is not None)
            or (pv_info.nodes is not None)
            or (pv_info.time is not None)
            or (pv_info.nps is not None)
            or (pv_info.hashfull is not None)
            or (pv_info.multipv is not None)
            or (pv_info.pv is not None)
            or (pv_info.string is not None)
        )
        return pv_info if has_any else None


class UsiThinkResult:
    """
    Final result for a ``go`` search: bestmove (and optional ponder).

    Also carries the latest PVs reported during thinking so callers can
    access evaluation and search statistics associated with the move.
    """

    def __init__(self) -> None:
        # Final Result (from bestmove)
        self.bestmove: Move | None = None
        self.ponder: Move | None = None

        # Thinking Info (accumulated from info)
        # Stores the latest PV for each multipv index
        self.pvs: list[UsiThinkPV] = []

        # Debug info strings collected during search (``info string ...``)
        self.info_strings: tuple[str, ...] = ()

    def get_last_pv(self, multipv_index: int = 1) -> UsiThinkPV | None:
        """Retrieves the last known PV for a given multipv index."""
        # Find the last PV with the given multipv index
        for pv in reversed(self.pvs):
            if pv.multipv == multipv_index or (pv.multipv is None and multipv_index == 1):
                return pv
        return None

    def __str__(self) -> str:
        return self.to_debug_string()

    def __repr__(self) -> str:
        return self.to_debug_string()

    def to_debug_string(self) -> str:
        """Format a compact debug string for logging."""
        ponder_str = f" ponder {self.ponder.to_usi()}" if self.ponder is not None else ""
        bestmove_str = self.bestmove.to_usi() if self.bestmove is not None else "(none)"
        return f"UsiThinkResult(bestmove:{bestmove_str}{ponder_str})"

    def to_usi_string(self) -> str:
        """Format a USI ``bestmove ... [ponder ...]`` string.

        Raises:
            ValueError: if ``bestmove`` is not set.
        """
        if self.bestmove is None:
            raise ValueError("bestmove is not set")

        result_str = f"bestmove {self.bestmove.to_usi()}"
        if self.ponder is not None:
            result_str += f" ponder {self.ponder.to_usi()}"
        return result_str

    @classmethod
    def from_string(cls, bestmove_line: str) -> "UsiThinkResult":
        """Parse a USI ``bestmove ...`` line into ``UsiThinkResult``."""
        parts = bestmove_line.strip().split()
        if not parts or parts[0] != "bestmove":
            raise ValueError(f"Invalid bestmove line: {bestmove_line}")

        result = cls()
        if len(parts) >= 2:
            try:
                result.bestmove = move_from_usi(parts[1])
            except ValueError as exc:
                raise ValueError(f"Invalid bestmove in line: {bestmove_line}") from exc
        else:
            raise ValueError(f"Missing bestmove move in line: {bestmove_line}")

        if "ponder" in parts:
            ponder_index = parts.index("ponder")
            if ponder_index + 1 < len(parts):
                try:
                    result.ponder = move_from_usi(parts[ponder_index + 1])
                except ValueError:
                    pass  # 不正なponder手は無視

        return result
