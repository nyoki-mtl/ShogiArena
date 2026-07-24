from __future__ import annotations

import logging
import math
from collections.abc import Iterable

from shogiarena._core.shared.kernel.service_ports import GameRecordPlayers

from .btd_models import BTDEstimate
from .constants import _ELO_TO_LOGIT, _MAX_OPT_STEPS
from .game_encoding import aggregate_encoded_games, collect_engines, encode_games
from .likelihood import ll_and_grad
from .matrix_ops import invert_neg_def, numerical_hessian

logger = logging.getLogger(__name__)


def _comparison_graph_connected(engines: list[str], records: list[GameRecordPlayers]) -> bool:
    """Whether every engine is linked by a chain of games to every other.

    A disconnected graph leaves the relative level of the components unidentifiable, so its standard
    errors are meaningless (the ridge-regularized Hessian would otherwise report huge but
    finite SEs rather than failing outright).
    """
    if len(engines) <= 1:
        return True
    parent = {engine: engine for engine in engines}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for record in records:
        black = str(record.get("black_player") or "")
        white = str(record.get("white_player") or "")
        if black in parent and white in parent and black != white:
            parent[find(black)] = find(white)
    return len({find(engine) for engine in engines}) == 1


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

        # Aggregate before optimizing: the loop below runs up to _MAX_OPT_STEPS times over this
        # list, so keeping it per-game makes every estimate cost O(steps * games played).
        encoded_games = aggregate_encoded_games(encode_games(records))
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
        if cov_theta is not None and not _comparison_graph_connected(engines, records):
            # The Hessian is only ridge-regularized, so a disconnected graph yields huge-but-finite
            # SEs instead of a singular failure; drop them rather than report misleading precision.
            cov_theta = None
        if cov_theta is None:
            logger.warning(
                "BTD standard errors are unavailable (singular Hessian or disconnected comparison "
                "graph); they are reported as None"
            )

        free_engine_ratings, color_advantage, log_draw_tendency = _unpack(theta)
        nu = math.exp(log_draw_tendency)
        ratings: dict[str, float] = {}
        rating_se: dict[str, float | None] = {}

        for index, name in enumerate(free_names):
            ratings[name] = free_engine_ratings[index]
            rating_se[name] = None if cov_theta is None else math.sqrt(max(0.0, cov_theta[index][index]))
        ratings[anchor] = 0.0
        rating_se[anchor] = 0.0  # the anchor is an exact reference, not an estimate

        # When the covariance is unavailable, leave rating_cov as None too -- otherwise a non-None
        # dict (with only the anchor's zero covariances) would let pair_delta() report a misleading
        # 0.0 standard error for a disconnected matchup.
        rating_cov: dict[tuple[str, str], float] | None
        if cov_theta is None:
            rating_cov = None
        else:
            rating_cov = {}
            for i, name_i in enumerate(free_names):
                for j, name_j in enumerate(free_names):
                    rating_cov[(name_i, name_j)] = cov_theta[i][j]
            for name in free_names:
                rating_cov[(name, anchor)] = 0.0
                rating_cov[(anchor, name)] = 0.0
            rating_cov[(anchor, anchor)] = 0.0

        g_idx = len(free_names)
        z_idx = g_idx + 1
        # C9a: gamma_elo is the actual first-move (black) advantage. The model adds +gamma to black
        # and -gamma to white, so the black-vs-white gap is 2*gamma; report that, not half of it.
        gamma_elo = 2.0 * color_advantage * _ELO_TO_LOGIT
        # C9b: at equal strength the engines still play one game as black, so the draw-equivalence
        # rate is nu / (nu + cosh(gamma)) (reduces to nu / (1 + nu) when gamma = 0).
        cosh_gamma = math.cosh(color_advantage)
        draw_eq = nu / (nu + cosh_gamma)
        if cov_theta is None:
            gamma_elo_se = None
            nu_se = None
            draw_eq_se = None
        else:
            gamma_elo_se = 2.0 * math.sqrt(max(0.0, cov_theta[g_idx][g_idx])) * _ELO_TO_LOGIT
            var_z = cov_theta[z_idx][z_idx]
            var_g = cov_theta[g_idx][g_idx]
            cov_zg = cov_theta[z_idx][g_idx]
            nu_se = nu * math.sqrt(max(0.0, var_z))
            denom = (nu + cosh_gamma) ** 2
            d_draw_dz = cosh_gamma * nu / denom  # z = log(nu)
            d_draw_dg = -nu * math.sinh(color_advantage) / denom
            var_draw = d_draw_dz**2 * var_z + d_draw_dg**2 * var_g + 2.0 * d_draw_dz * d_draw_dg * cov_zg
            draw_eq_se = math.sqrt(max(0.0, var_draw))

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
