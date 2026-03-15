from __future__ import annotations

import math
from dataclasses import dataclass


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


__all__ = ["BTDEstimate"]
