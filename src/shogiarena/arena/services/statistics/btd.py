"""
Bradley-Terry-Davidson (BTD) rating estimator with color advantage.

This module estimates order-independent ratings for multiple engines by
maximizing the Davidson draw-extended Bradley-Terry likelihood across all
games, with a single global first-move (black) advantage parameter.

We do not depend on external optimizers. A simple Adam optimizer is used
for MLE, followed by a numerical Hessian for standard error/CI estimates.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from shogiarena.utils.types.types import GameResult

# Elo/logistic scale mapping: r (Elo) -> t (natural logit space)
_ELO_TO_LOGIT = 400.0 / math.log(10.0)
_MAX_OPT_STEPS = 2000


@dataclass
class PairDelta:
    engine_a: str
    engine_b: str
    delta_elo: float
    standard_error: float | None
    likelihood_of_superiority: float | None  # Likelihood that engine_a > engine_b under normal approx


@dataclass
class BTDEstimate:
    ratings: dict[str, float]  # Elo
    rating_se: dict[str, float]  # per-engine standard error in Elo
    # Optional covariance between engine ratings in Elo space
    rating_cov: dict[tuple[str, str], float] | None
    anchor: str  # engine used as fixed reference (R=0)
    gamma_elo: float  # Black advantage in Elo
    gamma_elo_se: float | None
    nu: float  # Draw tendency parameter (p_draw(eq) = nu/(1+nu))
    nu_se: float | None
    draw_eq: float  # Implied draw rate at equal strength
    draw_eq_se: float | None

    def pair_delta(self, engine_a: str, engine_b: str, cov: dict[tuple[str, str], float] | None = None) -> PairDelta:
        rating_a = self.ratings.get(engine_a, 0.0)
        rating_b = self.ratings.get(engine_b, 0.0)
        delta_rating = rating_a - rating_b
        se = None
        los = None
        if cov is not None:
            variance_a = cov.get((engine_a, engine_a), 0.0)
            variance_b = cov.get((engine_b, engine_b), 0.0)
            covariance_ab = cov.get((engine_a, engine_b), 0.0)
            variance_delta = max(0.0, variance_a + variance_b - 2.0 * covariance_ab)
            se = math.sqrt(variance_delta) if variance_delta > 0 else 0.0
            if se and se > 0:
                z_score = delta_rating / (se + 1e-12)
                # Normal CDF
                los = 0.5 * (1.0 + math.erf(z_score / math.sqrt(2.0)))
        return PairDelta(
            engine_a=engine_a,
            engine_b=engine_b,
            delta_elo=delta_rating,
            standard_error=se,
            likelihood_of_superiority=los,
        )


class BTDEstimator:
    """Estimate BTD ratings from game records.

    Games are dicts with keys: black_player, white_player, result (string).
    Results are expected to contain 'BLACK'/'WHITE'/ 'DRAW'. Others are ignored.
    """

    def __init__(self) -> None:
        pass

    def estimate(
        self,
        games: Iterable[dict[str, Any]],
        anchor_name: str | None = None,
        engine_names: Iterable[str] | None = None,
    ) -> BTDEstimate:
        records = list(games)
        # Prefer provided engine_names to include engines even before first game completes
        if engine_names is not None:
            engines = sorted(set(engine_names))
        else:
            engines = sorted(self._collect_engines(records))
        if len(engines) < 2:
            # Trivial case
            # Choose anchor from provided name if possible; else fall back to first name
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

        # Choose anchor: prefer provided name if present among engines; otherwise last in sorted order
        anchor = anchor_name if (anchor_name in engines) else engines[-1]
        idx: dict[str, int] = {}
        free_names: list[str] = []
        for e in engines:
            if e == anchor:
                continue
            idx[e] = len(idx)
            free_names.append(e)
        # parameter layout: [r_0..r_{E-2}, gamma, z], where z = log(nu)
        P = len(free_names) + 2

        # Initialize params
        theta = [0.0] * P
        # gamma in logit space (0 Elo)
        # z = log(nu); start with nu ~ 1.0 => draw_eq ~ 0.5
        theta[-1] = 0.0  # z

        # Adam optimizer settings (log-likelihood ascent)
        lr = 0.05
        beta1 = 0.9
        beta2 = 0.999
        eps = 1e-8
        m = [0.0] * P
        v = [0.0] * P
        t = 0

        def unpack(theta_values: list[float]) -> tuple[list[float], float, float]:
            rating_vector = theta_values[: len(free_names)]
            color_advantage = theta_values[len(free_names)] if P >= 2 else 0.0
            log_draw_tendency = theta_values[len(free_names) + 1] if P >= 3 else 0.0
            return rating_vector, color_advantage, log_draw_tendency

        # Pre-encode outcomes for speed
        enc = []
        for record in records:
            b = str(record.get("black_player", ""))
            w = str(record.get("white_player", ""))
            if not b or not w:
                continue
            bw, ww, dr = self._encode_result(record.get("result"))
            if (bw + ww + dr) == 0:
                continue
            enc.append((b, w, bw, ww, dr))
        if not enc:
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

        # Gradient ascent
        last_ll = -1e300
        for _ in range(_MAX_OPT_STEPS):
            t += 1
            ll, grad = self._ll_and_grad(theta, enc, engines, idx, anchor)
            # Adam update
            for i in range(P):
                grad_i = grad[i]
                m[i] = beta1 * m[i] + (1 - beta1) * grad_i
                v[i] = beta2 * v[i] + (1 - beta2) * (grad_i * grad_i)
                mhat = m[i] / (1 - beta1**t)
                vhat = v[i] / (1 - beta2**t)
                theta[i] = theta[i] + lr * mhat / (math.sqrt(vhat) + eps)

            # Convergence checks
            if abs(ll - last_ll) < 1e-7:
                break
            last_ll = ll

        # Numerical Hessian at optimum for covariance
        H = self._numerical_hessian(theta, enc, engines, idx, anchor)
        cov_theta = self._invert_neg_def(H)  # Covariance ≈ inv(-H)

        free_engine_ratings, color_advantage, log_draw_tendency = unpack(theta)
        nu = math.exp(log_draw_tendency)
        # Build ratings dict in Elo units
        ratings: dict[str, float] = {}
        rating_se: dict[str, float] = {}

        # Map covariance to Elo. Ratings are already in Elo units in theta
        # Build covariance map for names
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
        # Covariances involving anchor are 0 in this parameterization
        for name in free_names:
            rating_cov[(name, anchor)] = 0.0
            rating_cov[(anchor, name)] = 0.0
        rating_cov[(anchor, anchor)] = 0.0

        # Center ratings to mean=0 for presentation (keep anchor at ~0 by construction)
        # Not necessary since anchor is 0; keeping as-is.

        # Gamma in Elo
        g_idx = len(free_names)
        gamma_elo = color_advantage * _ELO_TO_LOGIT
        gamma_se = math.sqrt(max(0.0, cov_theta[g_idx][g_idx])) if cov_theta is not None else None
        gamma_elo_se = gamma_se * _ELO_TO_LOGIT if gamma_se is not None else None

        # nu and draw_eq
        z_idx = g_idx + 1
        log_draw_tendency_se = math.sqrt(max(0.0, cov_theta[z_idx][z_idx])) if cov_theta is not None else None
        nu_se = (nu * log_draw_tendency_se) if log_draw_tendency_se is not None else None
        draw_eq = nu / (1.0 + nu)
        # delta method: dp/dnu = 1/(1+nu)^2
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

    # --- internals -----------------------------------------------------
    def _collect_engines(self, games: Iterable[dict[str, Any]]) -> set[str]:
        names: set[str] = set()
        for game in games:
            b = game.get("black_player")
            w = game.get("white_player")
            if b:
                names.add(str(b))
            if w:
                names.add(str(w))
        return names

    @staticmethod
    def _encode_result(raw: Any) -> tuple[int, int, int]:
        if isinstance(raw, GameResult):
            return (
                1 if raw.is_black_win() else 0,
                1 if raw.is_white_win() else 0,
                1 if raw.is_draw() else 0,
            )
        if isinstance(raw, int):
            gr = GameResult(raw)
            return BTDEstimator._encode_result(gr)
        if isinstance(raw, str):
            stripped = raw.strip()
            if stripped.isdigit():
                return BTDEstimator._encode_result(GameResult(int(stripped)))
            res = stripped.upper()
            return (
                1 if "BLACK" in res else 0,
                1 if "WHITE" in res else 0,
                1 if "DRAW" in res else 0,
            )
        return (0, 0, 0)

    def _ll_and_grad(
        self,
        theta: list[float],
        enc_games: list[tuple[str, str, int, int, int]],
        engines: list[str],
        idx: dict[str, int],
        anchor: str,
    ) -> tuple[float, list[float]]:
        # Unpack
        free_count = len(engines) - 1
        r = theta[:free_count]
        gamma = theta[free_count] if len(theta) >= free_count + 1 else 0.0
        z = theta[free_count + 1] if len(theta) >= free_count + 2 else 0.0
        nu = math.exp(z)

        # Helper to get rating in Elo for an engine name
        def r_elo(name: str) -> float:
            if name == anchor:
                return 0.0
            return r[idx[name]]

        ll = 0.0
        grad = [0.0 for _ in theta]
        inv_scale = 1.0 / _ELO_TO_LOGIT

        for b, w, yb, yw, yd in enc_games:
            rb = r_elo(b)
            rw = r_elo(w)
            # Transform to logit space t = r / s
            xb = rb * inv_scale + gamma  # black gets +gamma
            xw = rw * inv_scale - gamma  # white gets -gamma

            A = math.exp(xb)
            B = math.exp(xw)
            G = math.sqrt(A * B)
            Z = A + B + 2.0 * nu * G
            if Z <= 0:
                continue

            # Log-likelihood
            if yb:
                ll += math.log(A) - math.log(Z)
            elif yw:
                ll += math.log(B) - math.log(Z)
            elif yd:
                ll += (math.log(2.0) + math.log(nu) + 0.5 * (xb + xw)) - math.log(Z)

            # Partials wrt xb, xw, z
            # dl/dx_black = yb + 0.5*yd - (A + nu*G)/Z
            # dl/dx_white = yw + 0.5*yd - (B + nu*G)/Z
            # dl/dz = yd - 2*nu*G/Z
            dl_dxb = float(yb) + 0.5 * float(yd) - (A + nu * G) / Z
            dl_dxw = float(yw) + 0.5 * float(yd) - (B + nu * G) / Z
            dl_dz = float(yd) - (2.0 * nu * G) / Z

            # Chain to parameters
            # r_b contributes to xb with factor (1/s), r_w to xw with (1/s)
            if b != anchor:
                grad[idx[b]] += dl_dxb * inv_scale
            if w != anchor:
                grad[idx[w]] += dl_dxw * inv_scale

            # gamma contributes +1 to xb and -1 to xw
            g_idx = free_count
            if len(grad) > g_idx:
                grad[g_idx] += dl_dxb - dl_dxw

            # z
            z_idx = free_count + 1
            if len(grad) > z_idx:
                grad[z_idx] += dl_dz

        return ll, grad

    def _numerical_hessian(
        self,
        theta: list[float],
        enc_games: list[tuple[str, str, int, int, int]],
        engines: list[str],
        idx: dict[str, int],
        anchor: str,
        h: float = 1e-4,
    ) -> list[list[float]]:
        P = len(theta)
        # Central differences on gradient: H[:, j] = (g(theta+h e_j) - g(theta-h e_j)) / (2h)
        _ = self._ll_and_grad(theta, enc_games, engines, idx, anchor)[1]
        H = [[0.0 for _ in range(P)] for _ in range(P)]
        for j in range(P):
            tp = list(theta)
            tm = list(theta)
            tp[j] += h
            tm[j] -= h
            gp = self._ll_and_grad(tp, enc_games, engines, idx, anchor)[1]
            gm = self._ll_and_grad(tm, enc_games, engines, idx, anchor)[1]
            for i in range(P):
                H[i][j] = (gp[i] - gm[i]) / (2.0 * h)
        # Ensure symmetry
        for i in range(P):
            for j in range(P):
                H_ij = 0.5 * (H[i][j] + H[j][i])
                H[i][j] = H_ij
        return H

    def _invert_neg_def(self, H: list[list[float]]) -> list[list[float]]:
        """Compute covariance ≈ inv(-H) with small damping if needed."""
        # Copy matrix and apply -1
        A = copy.deepcopy(H)
        n = len(A)
        for i in range(n):
            for j in range(n):
                A[i][j] = -A[i][j]

        # Add tiny ridge to improve conditioning
        ridge = 1e-8
        for i in range(n):
            A[i][i] += ridge

        # Gauss-Jordan inversion with partial pivoting
        Id = [[0.0] * n for _ in range(n)]
        for i in range(n):
            Id[i][i] = 1.0

        for col in range(n):
            # Pivot
            pivot = col
            max_val = abs(A[pivot][col])
            for r in range(col + 1, n):
                v = abs(A[r][col])
                if v > max_val:
                    max_val = v
                    pivot = r
            if max_val < 1e-12:
                # Singular; return zero covariances
                return [[0.0] * n for _ in range(n)]
            if pivot != col:
                A[col], A[pivot] = A[pivot], A[col]
                Id[col], Id[pivot] = Id[pivot], Id[col]

            # Normalize pivot row
            pv = A[col][col]
            inv_pv = 1.0 / pv
            for j in range(n):
                A[col][j] *= inv_pv
                Id[col][j] *= inv_pv

            # Eliminate other rows
            for r in range(n):
                if r == col:
                    continue
                factor = A[r][col]
                if factor == 0.0:
                    continue
                for j in range(n):
                    A[r][j] -= factor * A[col][j]
                    Id[r][j] -= factor * Id[col][j]

        return Id
