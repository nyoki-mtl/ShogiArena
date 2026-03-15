from __future__ import annotations

import math

from .constants import _ELO_TO_LOGIT
from .game_encoding import EncodedGame


def ll_and_grad(
    theta: list[float],
    encoded_games: list[EncodedGame],
    engines: list[str],
    idx: dict[str, int],
    anchor: str,
) -> tuple[float, list[float]]:
    free_count = len(engines) - 1
    ratings = theta[:free_count]
    gamma = theta[free_count] if len(theta) >= free_count + 1 else 0.0
    log_draw_tendency = theta[free_count + 1] if len(theta) >= free_count + 2 else 0.0
    nu = math.exp(log_draw_tendency)

    def rating_for(engine_name: str) -> float:
        if engine_name == anchor:
            return 0.0
        return ratings[idx[engine_name]]

    log_likelihood = 0.0
    grad = [0.0 for _ in theta]
    inv_scale = 1.0 / _ELO_TO_LOGIT

    for black_player, white_player, black_wins, white_wins, draws in encoded_games:
        rating_black = rating_for(black_player)
        rating_white = rating_for(white_player)
        xb = rating_black * inv_scale + gamma
        xw = rating_white * inv_scale - gamma

        exp_black = math.exp(xb)
        exp_white = math.exp(xw)
        geom_mean = math.sqrt(exp_black * exp_white)
        partition = exp_black + exp_white + 2.0 * nu * geom_mean
        if partition <= 0:
            continue

        if black_wins:
            log_likelihood += math.log(exp_black) - math.log(partition)
        elif white_wins:
            log_likelihood += math.log(exp_white) - math.log(partition)
        elif draws:
            log_likelihood += (math.log(2.0) + math.log(nu) + 0.5 * (xb + xw)) - math.log(partition)

        dl_dxb = float(black_wins) + 0.5 * float(draws) - (exp_black + nu * geom_mean) / partition
        dl_dxw = float(white_wins) + 0.5 * float(draws) - (exp_white + nu * geom_mean) / partition
        dl_dz = float(draws) - (2.0 * nu * geom_mean) / partition

        if black_player != anchor:
            grad[idx[black_player]] += dl_dxb * inv_scale
        if white_player != anchor:
            grad[idx[white_player]] += dl_dxw * inv_scale

        g_idx = free_count
        if len(grad) > g_idx:
            grad[g_idx] += dl_dxb - dl_dxw

        z_idx = free_count + 1
        if len(grad) > z_idx:
            grad[z_idx] += dl_dz

    return log_likelihood, grad


__all__ = ["ll_and_grad"]
