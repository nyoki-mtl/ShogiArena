from __future__ import annotations

import math
from collections.abc import Iterable

from shogiarena._core.shared.kernel.service_ports import GameRecordPlayers

from .btd_models import BTDEstimate
from .constants import _ELO_TO_LOGIT, _MAX_OPT_STEPS
from .game_encoding import collect_engines, encode_games
from .likelihood import ll_and_grad
from .matrix_ops import invert_neg_def, numerical_hessian


class BTDEstimator:
    """Estimate BTD ratings from game records."""

    def estimate(
        self,
        games: Iterable[GameRecordPlayers],
        anchor_name: str | None = None,
        engine_names: Iterable[str] | None = None,
    ) -> BTDEstimate:
        records = list(games)
        if engine_names is not None:
            engines = sorted(set(engine_names))
        else:
            engines = sorted(collect_engines(records))

        if len(engines) < 2:
            anchor_guess = anchor_name if (anchor_name in engines) else (engines[0] if engines else "")
            return BTDEstimate(
                ratings=dict.fromkeys(engines, 0.0),
                rating_se=dict.fromkeys(engines, 0.0),
                rating_cov=None,
                anchor=anchor_guess,
                gamma_elo=0.0,
                gamma_elo_se=None,
                nu=1.0,
                nu_se=None,
                draw_eq=0.5,
                draw_eq_se=None,
            )

        anchor = anchor_name if (anchor_name in engines) else engines[-1]
        idx: dict[str, int] = {}
        free_names: list[str] = []
        for engine_name in engines:
            if engine_name == anchor:
                continue
            idx[engine_name] = len(idx)
            free_names.append(engine_name)

        param_count = len(free_names) + 2
        theta = [0.0] * param_count

        lr = 0.05
        beta1 = 0.9
        beta2 = 0.999
        eps = 1e-8
        m = [0.0] * param_count
        v = [0.0] * param_count
        step = 0

        def _unpack(theta_values: list[float]) -> tuple[list[float], float, float]:
            ratings = theta_values[: len(free_names)]
            gamma = theta_values[len(free_names)] if param_count >= 2 else 0.0
            log_draw_tendency = theta_values[len(free_names) + 1] if param_count >= 3 else 0.0
            return ratings, gamma, log_draw_tendency

        encoded_games = encode_games(records)
        if not encoded_games:
            return BTDEstimate(
                ratings=dict.fromkeys(engines, 0.0),
                rating_se=dict.fromkeys(engines, 0.0),
                rating_cov=None,
                anchor=anchor,
                gamma_elo=0.0,
                gamma_elo_se=None,
                nu=1.0,
                nu_se=None,
                draw_eq=0.5,
                draw_eq_se=None,
            )

        last_ll = -1e300
        for _ in range(_MAX_OPT_STEPS):
            step += 1
            ll, grad = ll_and_grad(theta, encoded_games, engines, idx, anchor)
            for index in range(param_count):
                grad_i = grad[index]
                m[index] = beta1 * m[index] + (1 - beta1) * grad_i
                v[index] = beta2 * v[index] + (1 - beta2) * (grad_i * grad_i)
                m_hat = m[index] / (1 - beta1**step)
                v_hat = v[index] / (1 - beta2**step)
                theta[index] = theta[index] + lr * m_hat / (math.sqrt(v_hat) + eps)
            if abs(ll - last_ll) < 1e-7:
                break
            last_ll = ll

        hessian = numerical_hessian(
            theta,
            lambda values: ll_and_grad(values, encoded_games, engines, idx, anchor)[1],
        )
        cov_theta = invert_neg_def(hessian)

        free_engine_ratings, color_advantage, log_draw_tendency = _unpack(theta)
        nu = math.exp(log_draw_tendency)
        ratings: dict[str, float] = {}
        rating_se: dict[str, float] = {}
        rating_cov: dict[tuple[str, str], float] = {}

        for i, name_i in enumerate(free_names):
            for j, name_j in enumerate(free_names):
                rating_cov[(name_i, name_j)] = cov_theta[i][j]
        for index, name in enumerate(free_names):
            ratings[name] = free_engine_ratings[index]
            variance = cov_theta[index][index] if cov_theta is not None else 0.0
            rating_se[name] = math.sqrt(max(0.0, variance))
        ratings[anchor] = 0.0
        rating_se[anchor] = 0.0
        for name in free_names:
            rating_cov[(name, anchor)] = 0.0
            rating_cov[(anchor, name)] = 0.0
        rating_cov[(anchor, anchor)] = 0.0

        g_idx = len(free_names)
        gamma_elo = color_advantage * _ELO_TO_LOGIT
        gamma_se = math.sqrt(max(0.0, cov_theta[g_idx][g_idx])) if cov_theta is not None else None
        gamma_elo_se = gamma_se * _ELO_TO_LOGIT if gamma_se is not None else None

        z_idx = g_idx + 1
        log_draw_tendency_se = math.sqrt(max(0.0, cov_theta[z_idx][z_idx])) if cov_theta is not None else None
        nu_se = (nu * log_draw_tendency_se) if log_draw_tendency_se is not None else None
        draw_eq = nu / (1.0 + nu)
        draw_eq_se = (nu_se / ((1.0 + nu) ** 2)) if nu_se is not None else None

        return BTDEstimate(
            ratings=ratings,
            rating_se=rating_se,
            rating_cov=rating_cov,
            anchor=anchor,
            gamma_elo=gamma_elo,
            gamma_elo_se=gamma_elo_se,
            nu=nu,
            nu_se=nu_se,
            draw_eq=draw_eq,
            draw_eq_se=draw_eq_se,
        )


__all__ = ["BTDEstimator"]
