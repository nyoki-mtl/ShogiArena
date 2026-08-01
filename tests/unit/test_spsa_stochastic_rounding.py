from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.spsa.adapters.orchestrator_update_mixin import (
    SpsaOrchestratorUpdateMixin,
    _format_canonical_decimal,
)
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry


class _FixedRng(random.Random):
    """常に固定値を返す Random スタブ。"""

    def __init__(self, value: float) -> None:
        super().__init__()
        self._value = value

    def random(self) -> float:  # type: ignore[override]
        return self._value


def _rounder(int_rounding: str) -> SpsaOrchestratorUpdateMixin:
    holder = SpsaOrchestratorUpdateMixin.__new__(SpsaOrchestratorUpdateMixin)
    holder.config = SimpleNamespace(  # type: ignore[attr-defined]
        int_rounding=int_rounding,
        run_seed="00" * 32,
        experiment_name="rounding-golden",
        is_snap_float_to_step=False,
    )
    holder.run_dir = SimpleNamespace(name="rounding-golden")  # type: ignore[attr-defined]
    return holder


def _int_param(*, value: float, rounding: str, name: str = "p") -> ParamEntry:
    return ParamEntry(
        name=name,
        type="int",
        value=value,
        min=0,
        max=10,
        step=1,
        delta=0.1,
        comment="",
        is_not_used=False,
        option_name=f"Tune.{name}",
        value_encoding="integer",
        rounding=rounding,  # type: ignore[arg-type]
    )


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


def test_per_parameter_nearest_overrides_global_stochastic() -> None:
    rounder = _rounder("stochastic")
    plus, minus = rounder._build_engine_option_maps_for_pair(
        [_int_param(value=2.5, rounding="nearest")],
        [_int_param(value=3.5, rounding="nearest")],
        update_idx=4,
    )

    assert plus == {"Tune.p": 2}
    assert minus == {"Tune.p": 4}


def test_per_parameter_stochastic_overrides_global_none_for_single_map() -> None:
    rounder = _rounder("none")

    options = rounder._build_engine_option_map(
        [_int_param(value=2.2, rounding="stochastic")],
        rng=_FixedRng(0.9),
        should_allow_stochastic=True,
    )

    assert options == {"Tune.p": 3}


def test_stochastic_pair_uses_versioned_rng_and_shared_sample() -> None:
    rounder = _rounder("none")
    plus, minus = rounder._build_engine_option_maps_for_pair(
        [_int_param(value=2.2, rounding="stochastic")],
        [_int_param(value=5.2, rounding="stochastic")],
        update_idx=4,
        pair_idx=1,
    )

    assert plus == {"Tune.p": 2}
    assert minus == {"Tune.p": 5}
    assert minus["Tune.p"] - plus["Tune.p"] == 3


def test_stochastic_wire_values_stay_within_integral_bounds() -> None:
    rounder = _rounder("none")
    for update_idx in range(20):
        plus, minus = rounder._build_engine_option_maps_for_pair(
            [_int_param(value=0.0, rounding="stochastic")],
            [_int_param(value=10.0, rounding="stochastic")],
            update_idx=update_idx,
        )
        assert 0 <= plus["Tune.p"] <= 10
        assert 0 <= minus["Tune.p"] <= 10


def test_canonical_decimal_avoids_exponent_and_negative_zero() -> None:
    assert _format_canonical_decimal(1e-10, significant_digits=9) == "0.0000000001"
    assert _format_canonical_decimal(-0.0, significant_digits=9) == "0"
    assert _format_canonical_decimal(123.45, significant_digits=9) == "123.45"


def test_final_decimal_wire_value_is_checked_against_effective_bounds() -> None:
    rounder = _rounder("none")
    parameter = ParamEntry(
        name="p",
        type="float",
        value=0.9999999996,
        min=0.0,
        max=0.9999999996,
        step=0.1,
        delta=0.1,
        comment="",
        is_not_used=False,
        option_name="Tune.p",
        value_encoding="decimal",
        significant_digits=9,
    )

    with pytest.raises(ValueError, match="outside effective bounds"):
        rounder._build_engine_option_map([parameter])
