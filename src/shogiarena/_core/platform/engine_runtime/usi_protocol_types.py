"""USI protocol data types, evaluation values, and stateless protocol parsing."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from rshogi.core import Move

from .usi_option_parser import parse_option_attributes, parse_option_header

MAX_PLY = 246
VALUE_MATE = 32000
VALUE_MATE_IN_MAX_PLY = VALUE_MATE - MAX_PLY

_SPECIAL_MOVE_MAP: dict[str, Move] = {
    "resign": Move.MOVE_RESIGN,
    "win": Move.MOVE_WIN,
    "0000": Move.MOVE_NONE,
}

logger = logging.getLogger(__name__)


def move_from_usi(usi: str) -> Move:
    """USI手文字列を ``Move`` に変換する。"""
    special = _SPECIAL_MOVE_MAP.get(usi)
    if special is not None:
        return special
    return Move.from_usi(usi)


def _mate_in(ply: int) -> int:
    return VALUE_MATE - ply


def _mated_in(ply: int) -> int:
    return -VALUE_MATE + ply


def find_last_pv(pvs: Iterable[UsiThinkPV], multipv_index: int = 1) -> UsiThinkPV | None:
    for pv in reversed(tuple(pvs)):
        if pv.multipv == multipv_index or (pv.multipv is None and multipv_index == 1):
            return pv
    return None


class UsiEvalValue(int):
    def is_mate_score(self) -> bool:
        return VALUE_MATE_IN_MAX_PLY <= int(self) <= VALUE_MATE

    def is_mated_score(self) -> bool:
        value = int(self)
        mated_min = -VALUE_MATE
        mated_max = -VALUE_MATE_IN_MAX_PLY
        return mated_min <= value <= mated_max

    def to_string(self) -> str:
        if self.is_mate_score():
            mate_ply: int = VALUE_MATE - int(self)
            return f"mate {mate_ply}"
        if self.is_mated_score():
            mated_ply: int = int(self) - (-VALUE_MATE)
            return f"mate -{abs(mated_ply)}"
        return f"cp {int(self)}"

    @staticmethod
    def mate_in_ply(ply: int) -> UsiEvalValue:
        if ply < 0:
            raise ValueError("Mate ply must be non-negative")
        score = _mate_in(ply)
        if score < VALUE_MATE_IN_MAX_PLY:
            score = VALUE_MATE_IN_MAX_PLY
        return UsiEvalValue(score)

    @staticmethod
    def mated_in_ply(ply: int) -> UsiEvalValue:
        if ply < 0:
            raise ValueError("Mated ply must be non-negative")
        score = _mated_in(ply)
        mated_max = -VALUE_MATE_IN_MAX_PLY
        if score > mated_max:
            score = mated_max
        return UsiEvalValue(score)


class UsiBound(Enum):
    NONE = 0
    UPPER = 1
    LOWER = 2
    EXACT = 3

    def to_string(self) -> str:
        if self == UsiBound.UPPER:
            return "upperbound"
        if self == UsiBound.LOWER:
            return "lowerbound"
        return ""

    @classmethod
    def from_string(cls, s: str) -> UsiBound:
        s_lower = s.lower()
        if s_lower == "upperbound":
            return cls.UPPER
        if s_lower == "lowerbound":
            return cls.LOWER
        if s_lower == "exact":
            return cls.EXACT
        return cls.NONE


class UsiThinkPV:
    def __init__(self) -> None:
        self.pv: list[Move] | None = None
        self.eval: UsiEvalValue | None = None
        self.bound: UsiBound = UsiBound.NONE
        self.depth: int | None = None
        self.seldepth: int | None = None
        self.nodes: int | None = None
        self.time: int | None = None
        self.hashfull: int | None = None
        self.nps: int | None = None
        self.multipv: int | None = None
        self.string: str | None = None

    def __str__(self) -> str:
        return self.to_debug_string()

    def __repr__(self) -> str:
        return self.to_debug_string()

    def to_debug_string(self) -> str:
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

    @classmethod
    def from_info_string(cls, info_line: str) -> UsiThinkPV | None:
        if not info_line.startswith("info "):
            return None
        args = info_line.split()[1:]
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
                    except ValueError as exc:
                        logger.debug(
                            "Ignoring invalid PV move token in info line: token=%s line=%s",
                            raw_move,
                            info_line,
                            exc_info=exc,
                        )
                        break
                pv_info.pv = pv_moves if pv_moves else None
                break
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
                        # Inspect the textual sign rather than the int so that "mate -0" (which
                        # YaneuraOu emits for "being mated, distance unknown") is treated as a
                        # mated score; int("-0") == 0 would otherwise flip it to a winning mate.
                        is_mated = ply < 0 or mate_val.lstrip().startswith("-")
                        current_eval = (
                            UsiEvalValue.mated_in_ply(abs(ply)) if is_mated else UsiEvalValue.mate_in_ply(ply)
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
        if current_eval is not None:
            pv_info.eval = current_eval
            pv_info.bound = current_bound
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
    def __init__(self) -> None:
        self.bestmove: Move | None = None
        self.ponder: Move | None = None
        self.pvs: list[UsiThinkPV] = []
        self.info_strings: tuple[str, ...] = ()

    def get_last_pv(self, multipv_index: int = 1) -> UsiThinkPV | None:
        return find_last_pv(self.pvs, multipv_index=multipv_index)

    def __str__(self) -> str:
        return self.to_debug_string()

    def __repr__(self) -> str:
        return self.to_debug_string()

    def to_debug_string(self) -> str:
        ponder_str = f" ponder {self.ponder.to_usi()}" if self.ponder is not None else ""
        bestmove_str = self.bestmove.to_usi() if self.bestmove is not None else "(none)"
        return f"UsiThinkResult(bestmove:{bestmove_str}{ponder_str})"

    @classmethod
    def from_string(cls, line: str) -> UsiThinkResult:
        parts = line.strip().split()
        if not parts or parts[0] != "bestmove":
            raise ValueError(f"Invalid bestmove line: {line}")

        result = cls()
        if len(parts) >= 2:
            try:
                result.bestmove = move_from_usi(parts[1])
            except ValueError as exc:
                raise ValueError(f"Invalid bestmove in line: {line}") from exc
        else:
            raise ValueError(f"Missing bestmove move in line: {line}")

        if "ponder" in parts:
            ponder_index = parts.index("ponder")
            if ponder_index + 1 < len(parts):
                try:
                    result.ponder = move_from_usi(parts[ponder_index + 1])
                except ValueError as exc:
                    logger.debug("Ignoring invalid ponder move in bestmove line: %s", line, exc_info=exc)

        return result


@runtime_checkable
class AsyncUsiProcessBridgePort(Protocol):
    """USI engine process bridge abstraction."""

    @property
    def name(self) -> str: ...

    async def start_process(self) -> None: ...

    async def stop_process(self) -> None: ...

    async def send_line(self, command: str) -> None: ...

    def receive_lines(self) -> AsyncIterator[str]: ...

    def is_running(self) -> bool: ...


@dataclass(slots=True, frozen=True)
class UsiIdField:
    """Parsed ``id <key> <value>`` line."""

    key: str
    value: str


@dataclass(slots=True)
class UsiOption:
    """Structured representation of a USI ``option`` declaration."""

    name: str
    option_type: str
    default: str | None = None
    current: str | None = None
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()


class UsiProtocolParser:
    """Stateless helpers for parsing USI protocol output."""

    @staticmethod
    def parse_id(line: str) -> UsiIdField | None:
        if not line.startswith("id "):
            return None
        parts = line.split(maxsplit=2)
        if len(parts) != 3:
            raise ValueError(f"Invalid id line (expected 'id <key> <value>'): {line}")
        key, value = parts[1], parts[2].strip()
        if not key:
            raise ValueError(f"Invalid id line; missing key: {line}")
        if not value:
            raise ValueError(f"Invalid id line; missing value for key '{key}': {line}")
        return UsiIdField(key=key, value=value)

    @staticmethod
    def parse_option(line: str) -> UsiOption | None:
        if not line.startswith("option "):
            return None
        parts = line.split()
        name_tokens, option_type, cursor = parse_option_header(parts, line)
        default_value, min_value, max_value, variants = parse_option_attributes(parts, cursor, line)
        option_name = " ".join(name_tokens)

        return UsiOption(
            name=option_name,
            option_type=option_type,
            default=default_value,
            current=default_value,
            minimum=min_value,
            maximum=max_value,
            choices=tuple(variants),
        )

    @staticmethod
    def parse_info(line: str) -> UsiThinkPV | None:
        return UsiThinkPV.from_info_string(line)

    @staticmethod
    def parse_bestmove(line: str, *, pvs: Iterable[UsiThinkPV] | None = None) -> UsiThinkResult | None:
        if not line.startswith("bestmove"):
            return None
        result = UsiThinkResult.from_string(line)
        if pvs:
            result.pvs.extend(pvs)
        return result
