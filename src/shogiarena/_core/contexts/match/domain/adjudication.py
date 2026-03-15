"""
Game adjudication service for arena async system.

This module provides early game termination adjudication similar to cutechess and
fastshogi implementations:
- Resign adjudication: Game ends when one side has a decisive disadvantage
- Max plies adjudication: Game ends after maximum number of plies
"""

import logging
from dataclasses import dataclass
from enum import Enum

from shogiarena._core.shared.kernel.game_results import GameResult

logger = logging.getLogger(__name__)


class AdjudicationResult(Enum):
    """Adjudication result types."""

    RESIGN_BLACK = "resign_black"
    RESIGN_WHITE = "resign_white"
    MAX_PLIES = "max_plies"


@dataclass
class AdjudicationConfig:
    """
    Configuration for game adjudication.

    Args:
        is_resign_enabled: Enable resign adjudication
        resign_score_cp: Resign score threshold in centipawns (absolute value)
        resign_move_count: Consecutive moves required for resign
        is_resign_two_sided: Apply resign check to both sides (vs only losing side)

        is_max_plies_enabled: Enable max-plies adjudication
        max_plies: Maximum number of plies before draw
    """

    is_resign_enabled: bool = True
    resign_score_cp: int = 800  # 8.0 pawns
    resign_move_count: int = 8
    is_resign_two_sided: bool = True

    is_max_plies_enabled: bool = True
    max_plies: int = 320


class ResignTracker:
    """Tracks consecutive moves for resign adjudication."""

    def __init__(self, resign_score_cp: int, move_count: int, is_two_sided: bool = True) -> None:
        self.resign_score_cp = resign_score_cp
        self.required_move_count = move_count
        self.is_two_sided = is_two_sided
        self.consecutive_losing_moves_black = 0
        self.consecutive_losing_moves_white = 0
        logger.debug(
            f"ResignTracker initialized: score={resign_score_cp}cp, moves={move_count}, is_two_sided={is_two_sided}"
        )

    def update(self, eval_cp: int, score_type: str | None, is_side_to_move_black: bool) -> AdjudicationResult | None:
        """
        Update resign tracking with evaluation.

        Args:
            eval_cp: Evaluation in centipawns (positive = black advantage)
            score_type: Type of score ("cp", "mate", or None)
            is_side_to_move_black: Whether black was to move in the evaluated
                position (i.e., the engine that just played)

        Returns:
            AdjudicationResult if resign criteria met, None otherwise
        """
        # Handle mate scores immediately
        if score_type == "mate" and eval_cp is not None:
            # eval_cp contains mate distance when score_type is "mate"
            # Positive = black is mating, negative = white is mating
            if eval_cp > 0:  # Black is mating
                logger.debug(f"Resign adjudication: Black has mate in {eval_cp}")
                return AdjudicationResult.RESIGN_WHITE
            else:  # White is mating
                logger.debug(f"Resign adjudication: White has mate in {-eval_cp}")
                return AdjudicationResult.RESIGN_BLACK

        # Handle centipawn evaluations
        if eval_cp is None or score_type != "cp":
            # Reset counters if no valid eval
            self.consecutive_losing_moves_black = 0
            self.consecutive_losing_moves_white = 0
            return None

        # Convert eval to perspective of side to move
        eval_from_moving_side = eval_cp if is_side_to_move_black else -eval_cp

        # Check if moving side is losing badly
        if eval_from_moving_side <= -self.resign_score_cp:
            if self.is_two_sided:
                # Two-sided mode: count moves from both sides
                if is_side_to_move_black:
                    self.consecutive_losing_moves_black += 1
                else:
                    self.consecutive_losing_moves_white += 1

                # Check if combined count reaches threshold
                total_losing_moves = self.consecutive_losing_moves_black + self.consecutive_losing_moves_white

                if total_losing_moves >= self.required_move_count * 2:
                    # Determine who should resign based on current eval
                    if eval_cp > 0:  # Black is winning
                        logger.debug(f"Resign (2-sided): White resigns after {total_losing_moves} moves")
                        return AdjudicationResult.RESIGN_WHITE
                    else:  # White is winning
                        logger.debug(f"Resign (2-sided): Black resigns after {total_losing_moves} moves")
                        return AdjudicationResult.RESIGN_BLACK
            else:
                # One-sided mode: separate counters for each side
                if is_side_to_move_black:
                    self.consecutive_losing_moves_black += 1
                    self.consecutive_losing_moves_white = 0
                    if self.consecutive_losing_moves_black >= self.required_move_count:
                        logger.info(
                            f"Resign adjudication: Black losing for {self.consecutive_losing_moves_black} moves"
                        )
                        return AdjudicationResult.RESIGN_BLACK
                else:
                    self.consecutive_losing_moves_white += 1
                    self.consecutive_losing_moves_black = 0
                    if self.consecutive_losing_moves_white >= self.required_move_count:
                        logger.info(
                            f"Resign adjudication: White losing for {self.consecutive_losing_moves_white} moves"
                        )
                        return AdjudicationResult.RESIGN_WHITE
        else:
            # Reset counters if not losing badly
            if not self.is_two_sided:
                # One-sided mode: reset the counter for the side that's not losing
                if is_side_to_move_black:
                    self.consecutive_losing_moves_black = 0
                else:
                    self.consecutive_losing_moves_white = 0
            else:
                # Two-sided mode: reset both counters when position improves
                self.consecutive_losing_moves_black = 0
                self.consecutive_losing_moves_white = 0

        return None


class MaxPlyTracker:
    """Tracks total plies for max-plies adjudication."""

    def __init__(self, max_plies: int) -> None:
        self.max_plies = max_plies
        logger.debug(f"MaxPlyTracker initialized: max_plies={max_plies}")

    def update(self, ply: int) -> AdjudicationResult | None:
        """
        Check if maximum plies reached.

        Args:
            ply: Current ply (0-based)

        Returns:
            AdjudicationResult if max plies reached, None otherwise
        """
        if ply >= self.max_plies:
            logger.debug(f"Max plies adjudication: Reached {ply} plies (limit: {self.max_plies})")
            return AdjudicationResult.MAX_PLIES
        return None


class Adjudicator:
    """
    Main adjudication service that combines all adjudication types.
    """

    def __init__(self, config: AdjudicationConfig) -> None:
        """
        Initialize adjudicator with configuration.

        Args:
            config: Adjudication configuration
        """
        self.config = config

        # Initialize trackers based on configuration
        self.resign_tracker: ResignTracker | None = None
        if config.is_resign_enabled:
            self.resign_tracker = ResignTracker(
                config.resign_score_cp, config.resign_move_count, config.is_resign_two_sided
            )

        self.max_plies_tracker: MaxPlyTracker | None = None
        if config.is_max_plies_enabled:
            self.max_plies_tracker = MaxPlyTracker(config.max_plies)

        logger.debug(f"Adjudicator initialized with config: {config}")

    def update(
        self,
        eval_cp: int | None,
        score_type: str | None,
        ply: int,
        is_side_to_move_black: bool,
    ) -> GameResult | None:
        """
        Update adjudication state and check for game ending conditions.

        Args:
            eval_cp: Evaluation in centipawns (positive = black advantage) or mate distance
            score_type: Type of score ("cp", "mate", or None)
        ply: Current ply from game start (0-based)
        is_side_to_move_black: Whether black was to move when the evaluation was
            produced (i.e., the side that just played)

        Returns:
            GameResult if game should be adjudicated, None to continue
        """
        # Check resign adjudication (handles both cp and mate scores)
        if self.resign_tracker and eval_cp is not None:
            result = self.resign_tracker.update(eval_cp, score_type, is_side_to_move_black)
            if result == AdjudicationResult.RESIGN_BLACK:
                return GameResult.WHITE_WIN
            elif result == AdjudicationResult.RESIGN_WHITE:
                return GameResult.BLACK_WIN

        # Check max-plies adjudication
        if self.max_plies_tracker:
            result = self.max_plies_tracker.update(ply)
            if result == AdjudicationResult.MAX_PLIES:
                return GameResult.DRAW_BY_MAX_PLIES

        return None
