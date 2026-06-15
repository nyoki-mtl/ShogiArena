"""Rating calculation service for arena tournaments."""

import logging
from collections.abc import Iterable

from shogiarena._core.shared.kernel.database_types import GameRecordPlayers
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
        # Games already folded into the ratings, so a replay (resume) or an in-session re-run
        # cannot double-count the same game.
        self._applied_game_ids: set[str] = set()

    def update_ratings(
        self,
        black_player: str,
        white_player: str,
        game_result: GameResult,
        *,
        game_id: str | None = None,
    ) -> tuple[float, float]:
        """Update internal ratings based on game result.

        Args:
            black_player: Name of black player
            white_player: Name of white player
            game_result: Game result enum
            game_id: Optional game identifier; when given, the same game is applied at most once.

        Returns:
            Tuple of (new_black_rating, new_white_rating)
        """
        current = (
            self._current_ratings.get(black_player, self.initial_rating),
            self._current_ratings.get(white_player, self.initial_rating),
        )
        if game_id is not None:
            if game_id in self._applied_game_ids:
                return current
            self._applied_game_ids.add(game_id)

        black_rating, white_rating = current

        # Convert result to score
        black_score = game_result_score(game_result, Color.BLACK)
        if black_score is None:
            return current
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

    def restore_from_games(self, games: Iterable[GameRecordPlayers]) -> int:
        """Rebuild ratings by replaying completed games in order (idempotent per game_id).

        Used on resume so the ratings reflect every game in the database, not only those played
        after the restart. Returns the number of games applied.
        """
        applied = 0
        for game in games:
            black = str(game.get("black_player") or "")
            white = str(game.get("white_player") or "")
            game_name = str(game.get("game_name") or "")
            if not black or not white or not game_name or game_name in self._applied_game_ids:
                continue
            self.update_ratings(black, white, game["result"], game_id=game_name)
            applied += 1
        return applied
