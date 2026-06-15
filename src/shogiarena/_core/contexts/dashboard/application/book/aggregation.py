"""dashboard Book タブの集計ビルダー。Task 0018。

記録済み game の participation metadata（0015 の `book` provenance）と各局の
out-of-book 観測（0016 の `compute_out_of_book`）から、book fingerprint/path 単位の
集計 payload を構築する。

表示の意味づけは「実着手が指定 book 上の候補手集合に含まれたか（heuristic upper bound）」であり、
エンジンが実際に book 由来で指したことの断定ではない（信頼源:
`agent-docs/architecture/opening-book-and-openings.md` §6, §8）。

本モジュールは純粋な集計のみを行い、book file アクセスや HTTP には依存しない。
out-of-book ply は呼び出し側（book file を開ける層）が算出して入力する。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue

SideOutcome = Literal["win", "loss", "draw"]
Color = Literal["black", "white"]


@dataclass(frozen=True, slots=True)
class BookSideInput:
    """1 局の片側（先手 or 後手）の book 利用と結果。"""

    engine_name: str
    outcome: SideOutcome
    book: JsonObject | None
    out_of_book_ply: int | None = None


@dataclass(frozen=True, slots=True)
class BookGameInput:
    """1 局分の book 集計入力（先手・後手）。"""

    black: BookSideInput
    white: BookSideInput


def book_key(provenance: JsonObject | None) -> str | None:
    """book provenance から集計キーを導く。

    優先順: fingerprint の ``full_sha256`` → ``partial_sha256`` → ``resolved_path`` → ``basename``。
    book 無効（provenance None）や手掛かりが無い場合は ``None``。
    """

    if provenance is None:
        return None
    fingerprint = provenance.get("fingerprint")
    if isinstance(fingerprint, dict):
        for hash_key in ("full_sha256", "partial_sha256"):
            value = fingerprint.get(hash_key)
            if isinstance(value, str) and value:
                return value
        basename = fingerprint.get("basename")
        if isinstance(basename, str) and basename:
            return f"name:{basename}"
    resolved = provenance.get("resolved_path")
    if isinstance(resolved, str) and resolved:
        return f"path:{resolved}"
    return None


def _book_label(provenance: JsonObject | None) -> str | None:
    if provenance is None:
        return None
    fingerprint = provenance.get("fingerprint")
    if isinstance(fingerprint, dict):
        basename = fingerprint.get("basename")
        if isinstance(basename, str) and basename:
            return basename
    resolved = provenance.get("resolved_path")
    if isinstance(resolved, str) and resolved:
        return resolved.rsplit("/", 1)[-1]
    return None


@dataclass(slots=True)
class _Tally:
    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0

    def add(self, outcome: SideOutcome) -> None:
        self.games += 1
        if outcome == "win":
            self.wins += 1
        elif outcome == "loss":
            self.losses += 1
        else:
            self.draws += 1

    def to_payload(self) -> JsonObject:
        # draw を 0.5 とする勝率（games が 0 なら None）。
        win_rate: JsonValue = None
        if self.games:
            win_rate = (self.wins + 0.5 * self.draws) / self.games
        return {
            "games": self.games,
            "wins": self.wins,
            "losses": self.losses,
            "draws": self.draws,
            "win_rate": win_rate,
        }


@dataclass(slots=True)
class _BookAccumulator:
    key: str
    label: str | None = None
    resolved_path: str | None = None
    fingerprint: JsonObject | None = None
    options: JsonObject | None = None
    overall: _Tally = field(default_factory=_Tally)
    by_color: dict[str, _Tally] = field(default_factory=lambda: {"black": _Tally(), "white": _Tally()})
    engines: dict[str, _Tally] = field(default_factory=dict)
    out_of_book_plies: list[int] = field(default_factory=list)
    out_of_book_unbounded: int = 0

    def observe_provenance(self, provenance: JsonObject) -> None:
        if self.label is None:
            self.label = _book_label(provenance)
        if self.resolved_path is None:
            resolved = provenance.get("resolved_path")
            self.resolved_path = resolved if isinstance(resolved, str) else None
        if self.fingerprint is None:
            fingerprint = provenance.get("fingerprint")
            if isinstance(fingerprint, dict):
                self.fingerprint = dict(fingerprint)
        if self.options is None:
            options = provenance.get("options")
            if isinstance(options, dict):
                self.options = dict(options)

    def add_side(self, side: BookSideInput, color: Color) -> None:
        self.overall.add(side.outcome)
        self.by_color[color].add(side.outcome)
        engine_tally = self.engines.setdefault(side.engine_name, _Tally())
        engine_tally.add(side.outcome)
        if side.out_of_book_ply is not None:
            self.out_of_book_plies.append(side.out_of_book_ply)
        else:
            self.out_of_book_unbounded += 1

    def to_payload(self) -> JsonObject:
        histogram: dict[str, int] = {}
        for ply in self.out_of_book_plies:
            histogram[str(ply)] = histogram.get(str(ply), 0) + 1
        out_of_book: JsonObject = {
            "samples": len(self.out_of_book_plies),
            "unbounded": self.out_of_book_unbounded,
            "histogram": dict(sorted(histogram.items(), key=lambda kv: int(kv[0]))),
        }
        if self.out_of_book_plies:
            out_of_book["mean_ply"] = sum(self.out_of_book_plies) / len(self.out_of_book_plies)
            out_of_book["max_ply"] = max(self.out_of_book_plies)
            out_of_book["min_ply"] = min(self.out_of_book_plies)
        validation = "unknown"
        if self.fingerprint is not None:
            method = self.fingerprint.get("hash_method")
            validation = "missing" if method == "missing" else "fingerprinted"
        return {
            "key": self.key,
            "label": self.label,
            "resolved_path": self.resolved_path,
            "fingerprint": self.fingerprint,
            "options": self.options,
            "overall": self.overall.to_payload(),
            "by_color": {color: tally.to_payload() for color, tally in self.by_color.items()},
            "engines": {name: tally.to_payload() for name, tally in sorted(self.engines.items(), key=lambda kv: kv[0])},
            "out_of_book": out_of_book,
            "validation": validation,
        }


def build_book_aggregation(games: Sequence[BookGameInput]) -> JsonObject:
    """game 群から Book タブの集計 payload を構築する。

    各 game の先手・後手それぞれについて、利用した book を fingerprint/path キーで集計する。
    勝率は draw を 0.5 とし、先後別・engine 別内訳と out-of-book ply のヒストグラムを含む。
    """

    accumulators: dict[str, _BookAccumulator] = {}

    def handle_side(side: BookSideInput, color: Color) -> None:
        key = book_key(side.book)
        if key is None or side.book is None:
            return
        accumulator = accumulators.get(key)
        if accumulator is None:
            accumulator = _BookAccumulator(key=key)
            accumulators[key] = accumulator
        accumulator.observe_provenance(side.book)
        accumulator.add_side(side, color)

    for game in games:
        handle_side(game.black, "black")
        handle_side(game.white, "white")

    books = [
        accumulator.to_payload()
        for accumulator in sorted(accumulators.values(), key=lambda acc: acc.overall.games, reverse=True)
    ]
    return {
        "books": books,
        "book_count": len(books),
        "note": "out-of-book is a heuristic upper bound: it counts whether the actual move was among the "
        "specified book's candidate moves (clipped at BookMoves), not proof the engine moved from book.",
    }


__all__ = [
    "BookGameInput",
    "BookSideInput",
    "build_book_aggregation",
    "book_key",
]
