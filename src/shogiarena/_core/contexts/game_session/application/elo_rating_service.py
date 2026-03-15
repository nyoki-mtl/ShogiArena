"""Rating calculation service for arena tournaments."""

import logging

from shogiarena._core.shared.kernel.game_record_types import Color, game_result_score
from shogiarena._core.shared.kernel.game_results import GameResult

logger = logging.getLogger(__name__)


class EloRatingService:
    """Service for calculating and managing Elo ratings in tournaments.

    Provides consistent rating calculations with configurable parameters
    and support for rating progression tracking.
    """

    def __init__(
        self,
        initial_rating: float = 1500.0,
        k_factor: float = 16.0,
    ) -> None:
        """Initialize rating service.

        Args:
            initial_rating: Initial Elo rating for new players
            k_factor: K-factor for Elo calculations
        """
        self.initial_rating = initial_rating
        self.k_factor = k_factor
        # Add internal rating cache for incremental updates
        self._current_ratings: dict[str, float] = {}

    def update_ratings(self, black_player: str, white_player: str, game_result: GameResult) -> tuple[float, float]:
        """Update internal ratings based on game result.

        Args:
            black_player: Name of black player
            white_player: Name of white player
            game_result: Game result enum

        Returns:
            Tuple of (new_black_rating, new_white_rating)
        """
        # Get current ratings or use initial
        black_rating = self._current_ratings.get(black_player, self.initial_rating)
        white_rating = self._current_ratings.get(white_player, self.initial_rating)

        # Convert result to score
        black_score = game_result_score(game_result, Color.BLACK)
        if black_score is None:
            return (
                self._current_ratings.get(black_player, self.initial_rating),
                self._current_ratings.get(white_player, self.initial_rating),
            )
        white_score = 1.0 - black_score

        # Calculate expected scores
        expected_black_score = 1 / (1 + 10 ** ((white_rating - black_rating) / 400))
        expected_white_score = 1 - expected_black_score

        # Update ratings
        new_black_rating = black_rating + self.k_factor * (black_score - expected_black_score)
        new_white_rating = white_rating + self.k_factor * (white_score - expected_white_score)

        # Store updated ratings
        self._current_ratings[black_player] = new_black_rating
        self._current_ratings[white_player] = new_white_rating

        return new_black_rating, new_white_rating
