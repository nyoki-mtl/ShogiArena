from shogiarena._core.contexts.spsa.application.param_io import (
    ParamEntry,
    quantize_value,
)


def test_quantize_value_int_rounds_to_int_and_clamps() -> None:
    p = ParamEntry(
        name="IntParam",
        type="int",
        value=0.0,
        min=0.0,
        max=10.0,
        step=3.0,
        delta=1.0,
        comment="",
        is_not_used=False,
    )
    # Near 4 should snap to nearest integer
    assert quantize_value(p, 3.6) == 4.0
    # Above 10 clamps to 10
    assert quantize_value(p, 11.0) == 10.0
