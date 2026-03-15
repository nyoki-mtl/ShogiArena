from __future__ import annotations

import math

from shogiarena._core.shared.kernel.game_results import GameResult

# Elo/logistic scale mapping: r (Elo) -> t (natural logit space)
_ELO_TO_LOGIT = 400.0 / math.log(10.0)
_MAX_OPT_STEPS = 2000

GameResultInput = str | int | GameResult | None


__all__ = ["GameResultInput", "_ELO_TO_LOGIT", "_MAX_OPT_STEPS"]
