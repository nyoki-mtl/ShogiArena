from __future__ import annotations

import random
from types import SimpleNamespace

from shogiarena._core.contexts.spsa.adapters.orchestrator_update_mixin import (
    SpsaOrchestratorUpdateMixin,
)


class _FixedRng(random.Random):
    """常に固定値を返す Random スタブ。"""

    def __init__(self, value: float) -> None:
        super().__init__()
        self._value = value

    def random(self) -> float:  # type: ignore[override]
        return self._value


def _rounder(int_rounding: str) -> SpsaOrchestratorUpdateMixin:
    holder = SpsaOrchestratorUpdateMixin.__new__(SpsaOrchestratorUpdateMixin)
    holder.config = SimpleNamespace(int_rounding=int_rounding)  # type: ignore[attr-defined]
    return holder


def test_stochastic_round_handles_negative_values_symmetrically() -> None:
    rounder = _rounder("stochastic")

    # -3.2 == -4 + 0.8: round up to -3 with prob 0.8, down to -4 with prob 0.2.
    # 旧実装 (int() による切り捨て) では負の frac が負になり常に -3 となりバイアスした。
    assert rounder._stochastic_round(-3.2, _FixedRng(0.5)) == -3
    assert rounder._stochastic_round(-3.2, _FixedRng(0.1)) == -4


def test_stochastic_round_handles_positive_values() -> None:
    rounder = _rounder("stochastic")

    # 2.3 == 2 + 0.3: rounds up to 3 only when 2.3 + u >= 3, i.e. u >= 0.7.
    assert rounder._stochastic_round(2.3, _FixedRng(0.8)) == 3
    assert rounder._stochastic_round(2.3, _FixedRng(0.5)) == 2


def test_stochastic_round_nearest_when_disabled() -> None:
    rounder = _rounder("nearest")

    assert rounder._stochastic_round(-3.2, _FixedRng(0.99)) == -3
    assert rounder._stochastic_round(2.6, _FixedRng(0.01)) == 3
