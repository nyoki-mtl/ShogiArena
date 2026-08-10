"""Replay folded games on an ``rsshogi`` board.

KI2 notation is taken *before* the move is applied, because the reading of a move
depends on the position it is played from.
"""

from __future__ import annotations

from collections.abc import Sequence

from rsshogi.core import Board, Move

from shogiarena._core.contexts.csa_watch.ports.replay_ports import (
    ReplayedPly,
    ReplayFailure,
    ReplayResult,
)


class RsshogiBoardReplay:
    """``BoardReplayPort`` backed by the real move generator."""

    def replay(self, initial_sfen: str, usi_moves: Sequence[str]) -> ReplayResult:
        try:
            board = Board(initial_sfen)
        except (ValueError, RuntimeError) as exc:
            return ReplayResult(
                plies=(),
                failure=ReplayFailure(ply=0, usi="", reason=f"invalid initial position: {exc}"),
            )

        applied: list[ReplayedPly] = []
        for index, usi in enumerate(usi_moves):
            ply = index + 1
            sfen_before = board.to_sfen()
            try:
                move = Move.from_usi(usi)
            except (ValueError, RuntimeError) as exc:
                return ReplayResult(
                    plies=tuple(applied),
                    failure=ReplayFailure(ply=ply, usi=usi, reason=f"unreadable move: {exc}"),
                )
            if not board.is_legal_move(move):
                return ReplayResult(
                    plies=tuple(applied),
                    failure=ReplayFailure(ply=ply, usi=usi, reason="illegal move for this position"),
                )
            ki2 = move.to_ki2(board)
            # `to_csa` already carries the mover's sign; adding one doubles it.
            csa = board.move32_from_move(move).to_csa()
            try:
                board.apply_move(move)
            except (ValueError, RuntimeError) as exc:
                return ReplayResult(
                    plies=tuple(applied),
                    failure=ReplayFailure(ply=ply, usi=usi, reason=f"could not be applied: {exc}"),
                )
            applied.append(
                ReplayedPly(
                    ply=ply,
                    usi=usi,
                    ki2=ki2,
                    csa=csa,
                    sfen_before=sfen_before,
                    sfen_after=board.to_sfen(),
                    move_value=int(move),
                )
            )
        return ReplayResult(plies=tuple(applied))


__all__ = ["RsshogiBoardReplay"]
