"""
Sequential Probability Ratio Test (SPRT) service for arena async system.

This module provides SPRT implementation for automatic statistical testing,
based on cutechess sprt.cpp implementation.
"""

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import TypedDict

from shogiarena.utils.types.types import GameResult

logger = logging.getLogger(__name__)


class SprtStateSnapshot(TypedDict):
    """SPRT ステートのスナップショット（pause/resume 用）。"""

    elo0: float
    elo1: float
    alpha: float
    beta: float
    wins: int
    draws: int
    losses: int
    games_played: int
    llr: float


class SprtDecision(Enum):
    """SPRT test decision states."""

    CONTINUE = "continue"
    ACCEPT_H0 = "accept_h0"  # Null hypothesis (no improvement)
    ACCEPT_H1 = "accept_h1"  # Alternative hypothesis (improvement)


@dataclass
class SprtResult:
    """SPRT test result information."""

    llr: float  # Log Likelihood Ratio
    lower_bound: float  # Lower bound (H0 acceptance)
    upper_bound: float  # Upper bound (H1 acceptance)
    decision: SprtDecision
    games_played: int
    wins: int
    draws: int
    losses: int
    win_rate: float
    elo_estimate: float | None = None


class Sprt:
    """
    Sequential Probability Ratio Test implementation.

    Tests whether the true Elo difference is closer to elo0 (H0) or elo1 (H1)
    using Type I and Type II error rates alpha and beta.
    """

    def __init__(self, elo0: float, elo1: float, alpha: float = 0.05, beta: float = 0.05) -> None:
        """
        Initialize SPRT test.

        Args:
            elo0: Null hypothesis Elo difference (typically 0)
            elo1: Alternative hypothesis Elo difference (e.g., 5.0)
            alpha: Type I error rate (false positive, rejecting true H0)
            beta: Type II error rate (false negative, accepting false H0)
        """
        if elo1 <= elo0:
            raise ValueError(f"elo1 ({elo1}) must be greater than elo0 ({elo0})")
        if not (0 < alpha < 1) or not (0 < beta < 1):
            raise ValueError("alpha and beta must be between 0 and 1")

        self.elo0 = elo0
        self.elo1 = elo1
        self.alpha = alpha
        self.beta = beta

        # Calculate bounds
        self.lower_bound = math.log(beta / (1 - alpha))
        self.upper_bound = math.log((1 - beta) / alpha)

        # Game results
        self.wins = 0
        self.draws = 0
        self.losses = 0
        self.games_played = 0
        self.llr = 0.0

        logger.debug(f"SPRT initialized: H0={elo0}, H1={elo1}, alpha={alpha}, beta={beta}")
        logger.debug(f"SPRT bounds: lower={self.lower_bound:.4f}, upper={self.upper_bound:.4f}")

    def to_snapshot(self) -> dict[str, float | int]:
        """Serialize SPRT state for pause/resume."""
        return {
            "elo0": self.elo0,
            "elo1": self.elo1,
            "alpha": self.alpha,
            "beta": self.beta,
            "wins": self.wins,
            "draws": self.draws,
            "losses": self.losses,
            "games_played": self.games_played,
            "llr": self.llr,
        }

    @classmethod
    def from_snapshot(cls, snapshot: SprtStateSnapshot) -> "Sprt":
        """Restore SPRT state from a snapshot."""
        sprt = cls(
            elo0=float(snapshot.get("elo0", 0.0)),
            elo1=float(snapshot.get("elo1", 0.0)),
            alpha=float(snapshot.get("alpha", 0.05)),
            beta=float(snapshot.get("beta", 0.05)),
        )
        sprt.wins = int(snapshot.get("wins", 0))
        sprt.draws = int(snapshot.get("draws", 0))
        sprt.losses = int(snapshot.get("losses", 0))
        sprt.games_played = int(snapshot.get("games_played", 0))
        sprt.llr = float(snapshot.get("llr", 0.0))
        return sprt

    def add_game_result(self, result: GameResult) -> SprtResult:
        """
        Add game result and update SPRT state.

        Args:
            result: Game result from perspective of tested engine

        Returns:
            Current SPRT result with decision
        """
        # Update game counts
        self.games_played += 1
        if result == GameResult.WHITE_WIN:  # Assuming tested engine is white
            self.wins += 1
        elif result == GameResult.BLACK_WIN:
            self.losses += 1
        elif result.is_draw():  # Any type of draw
            self.draws += 1
        else:
            # For other results (errors, etc.), treat as draws for now
            self.draws += 1

        # Update LLR
        self._update_llr(result)

        # Make decision
        decision = self._make_decision()

        # Calculate win rate and Elo estimate
        win_rate = (self.wins + 0.5 * self.draws) / max(1, self.games_played)
        elo_estimate = self._win_rate_to_elo(win_rate) if self.games_played > 0 else None

        result_obj = SprtResult(
            llr=self.llr,
            lower_bound=self.lower_bound,
            upper_bound=self.upper_bound,
            decision=decision,
            games_played=self.games_played,
            wins=self.wins,
            draws=self.draws,
            losses=self.losses,
            win_rate=win_rate,
            elo_estimate=elo_estimate,
        )

        logger.debug(f"SPRT updated: games={self.games_played}, LLR={self.llr:.4f}, decision={decision.value}")
        return result_obj

    def _update_llr(self, result: GameResult) -> None:
        """Update Log Likelihood Ratio based on game result."""
        # Convert Elo differences to win probabilities
        # P(win) = 1 / (1 + 10^(-elo/400))
        p0 = 1.0 / (1.0 + math.pow(10.0, -self.elo0 / 400.0))
        p1 = 1.0 / (1.0 + math.pow(10.0, -self.elo1 / 400.0))

        # Calculate likelihood ratio for this result
        if result == GameResult.WHITE_WIN:
            # Win
            if p1 > 0 and p0 > 0:
                lr = p1 / p0
            else:
                lr = 1.0
        elif result == GameResult.BLACK_WIN:
            # Loss
            if (1 - p1) > 0 and (1 - p0) > 0:
                lr = (1 - p1) / (1 - p0)
            else:
                lr = 1.0
        elif result.is_draw():
            # Draw - using simplified model where draw probability is constant
            # In practice, this could be more sophisticated
            lr = 1.0
        else:
            # Other results (error, etc.) - neutral
            lr = 1.0

        # Update LLR
        if lr > 0:
            self.llr += math.log(lr)

    def _make_decision(self) -> SprtDecision:
        """Make SPRT decision based on current LLR and bounds."""
        if self.llr >= self.upper_bound:
            return SprtDecision.ACCEPT_H1
        elif self.llr <= self.lower_bound:
            return SprtDecision.ACCEPT_H0
        else:
            return SprtDecision.CONTINUE

    def _win_rate_to_elo(self, win_rate: float) -> float:
        """
        Convert win rate to Elo difference estimate.

        Args:
            win_rate: Win rate (0.0 to 1.0)

        Returns:
            Estimated Elo difference
        """
        # Clamp win rate to avoid log(0)
        win_rate = max(0.001, min(0.999, win_rate))

        # Elo = -400 * log10(1/win_rate - 1)
        return -400.0 * math.log10(1.0 / win_rate - 1.0)

    def get_status(self) -> SprtResult:
        """Get current SPRT status without adding a new result."""
        decision = self._make_decision()
        win_rate = (self.wins + 0.5 * self.draws) / max(1, self.games_played)
        elo_estimate = self._win_rate_to_elo(win_rate) if self.games_played > 0 else None

        return SprtResult(
            llr=self.llr,
            lower_bound=self.lower_bound,
            upper_bound=self.upper_bound,
            decision=decision,
            games_played=self.games_played,
            wins=self.wins,
            draws=self.draws,
            losses=self.losses,
            win_rate=win_rate,
            elo_estimate=elo_estimate,
        )

    def reset(self) -> None:
        """Reset SPRT state for a new test."""
        self.wins = 0
        self.draws = 0
        self.losses = 0
        self.games_played = 0
        self.llr = 0.0
        logger.debug("SPRT reset")

    def is_finished(self) -> bool:
        """Check if SPRT test has reached a decision."""
        decision = self._make_decision()
        return decision != SprtDecision.CONTINUE

    def __str__(self) -> str:
        """String representation for debugging."""
        status = self.get_status()
        return (
            f"SPRT(games={status.games_played}, LLR={status.llr:.4f}, "
            f"bounds=[{status.lower_bound:.4f}, {status.upper_bound:.4f}], "
            f"decision={status.decision.value}, elo_est={status.elo_estimate:.1f})"
        )
