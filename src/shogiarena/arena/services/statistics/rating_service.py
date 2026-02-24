"""Rating calculation service for arena tournaments."""

import logging
import math
from typing import TypedDict

from shogiarena.utils.types.types import Color, GameRecordDict, GameResult, game_result_score

logger = logging.getLogger(__name__)


class RatingSeriesEntry(TypedDict):
    engine: str
    points: list[tuple[int, float]]


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

    def calculate_rating_series(self, games: list[GameRecordDict]) -> list[RatingSeriesEntry]:
        """Calculate rating progression series from game results.

        Args:
            games: List of game records with player names and results

        Returns:
            List of rating series data for each engine
        """
        ratings: dict[str, float] = {}
        history: dict[str, list[tuple[int, float]]] = {}

        # Collect all unique engine names
        engines: set[str] = set()
        for game in games:
            engines.add(game["black_player"])
            engines.add(game["white_player"])

        # Initialize with baseline ratings at game index 0
        for engine_name in engines:
            ratings[engine_name] = self.initial_rating
            history[engine_name] = [(0, self.initial_rating)]

        # Process each game in order
        for game_idx, game in enumerate(games, start=1):
            black_player = game["black_player"]
            white_player = game["white_player"]
            result: GameResult = game["result"]

            # Ensure players exist in ratings
            if black_player not in ratings:
                ratings[black_player] = self.initial_rating
                history.setdefault(black_player, [(0, self.initial_rating)])
            if white_player not in ratings:
                ratings[white_player] = self.initial_rating
                history.setdefault(white_player, [(0, self.initial_rating)])

            # Get current ratings
            black_rating = ratings[black_player]  # Black rating
            white_rating = ratings[white_player]  # White rating

            # Calculate expected scores
            expected_black_score = 1.0 / (1.0 + 10 ** ((white_rating - black_rating) / 400.0))
            expected_white_score = 1.0 - expected_black_score

            # Determine actual scores from result
            # Convert to scores; skip paused/error/invalid
            black_score = game_result_score(result, Color.BLACK)
            if black_score is None:
                history[black_player].append((game_idx, ratings[black_player]))
                history[white_player].append((game_idx, ratings[white_player]))
                continue
            white_score = 1.0 - black_score

            # Update ratings using Elo formula
            updated_black_rating = black_rating + self.k_factor * (black_score - expected_black_score)
            updated_white_rating = white_rating + self.k_factor * (white_score - expected_white_score)

            ratings[black_player] = updated_black_rating
            ratings[white_player] = updated_white_rating

            # Record in history
            history[black_player].append((game_idx, updated_black_rating))
            history[white_player].append((game_idx, updated_white_rating))

        # Convert to series format for dashboard/visualization
        rating_series: list[RatingSeriesEntry] = []
        for engine_name, points in sorted(history.items(), key=lambda x: x[0]):
            rating_series.append({"engine": engine_name, "points": points})

        return rating_series

    def calculate_rating_confidence_intervals(
        self,
        games: list[GameRecordDict],
        *,
        confidence: float = 0.95,
    ) -> dict[str, tuple[float, float]]:
        """Calculate approximate Elo confidence intervals per engine.

        Uses a normal approximation on the win-rate estimate and converts to Elo.
        This is a lightweight estimate intended for dashboard visibility.
        """
        if not (0 < confidence < 1):
            raise ValueError("confidence must be between 0 and 1")
        z = _z_value(confidence)
        ratings = self.get_current_ratings(games)
        stats: dict[str, dict[str, int]] = {}
        for game in games:
            black = game["black_player"]
            white = game["white_player"]
            result: GameResult = game["result"]
            stats.setdefault(black, {"wins": 0, "draws": 0, "losses": 0})
            stats.setdefault(white, {"wins": 0, "draws": 0, "losses": 0})
            if result.is_draw():
                stats[black]["draws"] += 1
                stats[white]["draws"] += 1
            elif result.is_black_win():
                stats[black]["wins"] += 1
                stats[white]["losses"] += 1
            elif result.is_white_win():
                stats[white]["wins"] += 1
                stats[black]["losses"] += 1

        intervals: dict[str, tuple[float, float]] = {}
        for engine, rating in ratings.items():
            counts = stats.get(engine, {"wins": 0, "draws": 0, "losses": 0})
            games_played = counts["wins"] + counts["draws"] + counts["losses"]
            if games_played <= 0:
                intervals[engine] = (rating, rating)
                continue
            p = (counts["wins"] + 0.5 * counts["draws"]) / games_played
            p = min(0.999, max(0.001, p))
            se = (400.0 / math.log(10.0)) * math.sqrt(1.0 / (games_played * p * (1.0 - p)))
            margin = z * se
            intervals[engine] = (rating - margin, rating + margin)
        return intervals

    def get_rating_series_for_engine(self, games: list[GameRecordDict], engine_name: str) -> list[tuple[int, float]]:
        """Return rating history for a single engine."""
        series = self.calculate_rating_series(games)
        for entry in series:
            if entry.get("engine") == engine_name:
                return list(entry.get("points", []))
        return []

    def get_current_ratings(self, games: list[GameRecordDict]) -> dict[str, float]:
        """Get current ratings after processing all games.

        Args:
            games: List of game records

        Returns:
            Dictionary mapping engine names to current ratings
        """
        ratings: dict[str, float] = {}

        # Collect engine names
        engines: set[str] = set()
        for game in games:
            engines.add(game["black_player"])
            engines.add(game["white_player"])

        # Initialize ratings
        for engine_name in engines:
            ratings[engine_name] = self.initial_rating

        # Process games
        for game in games:
            black_player = game["black_player"]
            white_player = game["white_player"]
            result: GameResult = game["result"]

            # Ensure players exist
            if black_player not in ratings:
                ratings[black_player] = self.initial_rating
            if white_player not in ratings:
                ratings[white_player] = self.initial_rating

            # Calculate and update ratings
            black_rating = ratings[black_player]
            white_rating = ratings[white_player]

            expected_black_score = 1.0 / (1.0 + 10 ** ((white_rating - black_rating) / 400.0))
            expected_white_score = 1.0 - expected_black_score

            black_score = game_result_score(result, Color.BLACK)
            if black_score is None:
                continue
            white_score = 1.0 - black_score

            ratings[black_player] = black_rating + self.k_factor * (black_score - expected_black_score)
            ratings[white_player] = white_rating + self.k_factor * (white_score - expected_white_score)

        return ratings

    def calculate_rating_difference(self, engine_a: str, engine_b: str, games: list[GameRecordDict]) -> float:
        """Calculate rating difference between two specific engines.

        Args:
            engine_a: First engine name
            engine_b: Second engine name
            games: List of game records

        Returns:
            Rating difference (A - B)
        """
        ratings = self.get_current_ratings(games)
        rating_for_a = ratings.get(engine_a, self.initial_rating)
        rating_for_b = ratings.get(engine_b, self.initial_rating)
        return rating_for_a - rating_for_b

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


def _z_value(confidence: float) -> float:
    if confidence >= 0.99:
        return 2.575
    if confidence >= 0.98:
        return 2.326
    if confidence >= 0.95:
        return 1.96
    if confidence >= 0.90:
        return 1.645
    return 1.0
