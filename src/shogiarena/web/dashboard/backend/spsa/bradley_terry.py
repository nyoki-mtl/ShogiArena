"""Utility helpers for Bradley-Terry style rating estimation."""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


ELO_SCALE: float = 400.0 / math.log(10.0)
_MIN_STRENGTH = 1e-6


@dataclass(frozen=True)
class BradleyTerryMatch:
    """Aggregated match statistics between two variants."""

    player_a: int
    player_b: int
    wins_a: float
    wins_b: float
    draws: float = 0.0


@dataclass(slots=True)
class BradleyTerryEstimate:
    """Result of Bradley-Terry estimation."""

    theta: dict[int, float]
    strengths: dict[int, float]
    variances: dict[int, float | None]


def theta_to_elo(theta: float) -> float:
    """Convert a log-strength value to Elo difference."""

    return theta * ELO_SCALE


def estimate_bradley_terry(
    matches: Iterable[BradleyTerryMatch],
    *,
    baseline_id: int,
    prior_strength: float = 0.5,
    max_iter: int = 64,
    tol: float = 1e-8,
) -> BradleyTerryEstimate:
    """Estimate Bradley-Terry ratings from aggregated match data.

    Parameters
    ----------
    matches:
        Iterable of aggregated match statistics.
    baseline_id:
        Identifier of the baseline variant that acts as the rating anchor.
    prior_strength:
        Symmetric pseudo-count added to every observed pairing to stabilise
        the estimate. Expressed in games.
    max_iter:
        Maximum number of MM iterations.
    tol:
        Convergence tolerance on the strength parameters.
    """

    pair_stats: dict[tuple[int, int], dict[str, float]] = {}
    players: set[int] = {baseline_id}

    for match in matches:
        if match.player_a == match.player_b:
            continue

        wins_a = max(0.0, float(match.wins_a))
        wins_b = max(0.0, float(match.wins_b))
        draws = max(0.0, float(match.draws))
        wins_a += draws * 0.5
        wins_b += draws * 0.5
        games = wins_a + wins_b
        if games <= 0:
            continue

        a = int(match.player_a)
        b = int(match.player_b)
        key = (a, b) if a < b else (b, a)
        if a < b:
            wins_forward = wins_a
            wins_reverse = wins_b
        else:
            wins_forward = wins_b
            wins_reverse = wins_a

        data = pair_stats.setdefault(key, {"wins_ab": 0.0, "wins_ba": 0.0, "games": 0.0})
        data["wins_ab"] += wins_forward
        data["wins_ba"] += wins_reverse
        data["games"] += games

        players.add(a)
        players.add(b)

    strengths: dict[int, float]
    theta: dict[int, float]
    variances: dict[int, float | None]

    if not pair_stats:
        theta = dict.fromkeys(players, 0.0)
        strengths = {player: 1.0 if player == baseline_id else _MIN_STRENGTH for player in players}
        variances = {player: 0.0 if player == baseline_id else None for player in players}
        return BradleyTerryEstimate(theta=theta, strengths=strengths, variances=variances)

    # Apply symmetric pseudo-counts per observed pairing
    if prior_strength > 0:
        half_prior = prior_strength * 0.5
        for data in pair_stats.values():
            data["wins_ab"] += half_prior
            data["wins_ba"] += half_prior
            data["games"] += prior_strength

    # Build directional win table
    wins_matrix: dict[int, dict[int, float]] = {}
    total_wins: dict[int, float] = dict.fromkeys(players, 0.0)

    for (a, b), data in pair_stats.items():
        wins_ab = data["wins_ab"]
        wins_ba = data["wins_ba"]
        wins_matrix.setdefault(a, {})[b] = wins_ab
        wins_matrix.setdefault(b, {})[a] = wins_ba
        total_wins[a] = total_wins.get(a, 0.0) + wins_ab
        total_wins[b] = total_wins.get(b, 0.0) + wins_ba

    strengths = dict.fromkeys(players, 1.0)

    for _ in range(max_iter):
        delta = 0.0
        for player in players:
            wins_p = total_wins.get(player, 0.0)
            if wins_p <= 0:
                continue
            denom = 0.0
            opponents = wins_matrix.get(player, {})
            if not opponents:
                continue
            for opponent, wins_po in opponents.items():
                wins_op = wins_matrix.get(opponent, {}).get(player, 0.0)
                pair_games = wins_po + wins_op
                if pair_games <= 0:
                    continue
                denom += pair_games / (strengths[player] + strengths[opponent])
            if denom <= 0:
                continue
            updated = wins_p / denom
            if not math.isfinite(updated) or updated <= 0:
                updated = _MIN_STRENGTH
            delta = max(delta, abs(updated - strengths[player]))
            strengths[player] = updated

        anchor = strengths.get(baseline_id, 1.0)
        if not math.isfinite(anchor) or anchor <= 0:
            anchor = 1.0
        inv_anchor = 1.0 / anchor
        for player in strengths:
            strengths[player] = max(strengths[player] * inv_anchor, _MIN_STRENGTH)

        if delta < tol:
            break

    theta = {player: math.log(strength) for player, strength in strengths.items()}

    variances = dict.fromkeys(players)
    variances[baseline_id] = 0.0

    free_players = [player for player in players if player != baseline_id]
    size = len(free_players)
    if size > 0:
        info = np.zeros((size, size), dtype=float)
        index_map = {player: idx for idx, player in enumerate(free_players)}
        for (a, b), data in pair_stats.items():
            games_played = data["games"]
            if games_played <= 0:
                continue
            strength_a = strengths[a]
            strength_b = strengths[b]
            prob_a = strength_a / (strength_a + strength_b)
            weight = games_played * prob_a * (1.0 - prob_a)
            if weight <= 0:
                continue

            if a != baseline_id:
                ia = index_map[a]
                info[ia, ia] += weight
            if b != baseline_id:
                ib = index_map[b]
                info[ib, ib] += weight
            if a != baseline_id and b != baseline_id:
                ia = index_map[a]
                ib = index_map[b]
                info[ia, ib] -= weight
                info[ib, ia] -= weight

        if info.size:
            try:
                covariance = np.linalg.pinv(info, rcond=1e-9)
            except np.linalg.LinAlgError:  # pragma: no cover
                pass
            else:
                for player, idx in index_map.items():
                    value = covariance[idx, idx]
                    if math.isfinite(float(value)) and float(value) >= 0:
                        variances[player] = float(value)
                    else:
                        variances[player] = None

    return BradleyTerryEstimate(theta=theta, strengths=strengths, variances=variances)
