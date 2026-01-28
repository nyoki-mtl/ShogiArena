from shogiarena.arena.tuning.param_io import ParamEntry, compute_variant_id_from_entries, quantize_value


def test_quantize_value_int_rounds_to_int_and_clamps() -> None:
    p = ParamEntry(
        name="IntParam",
        type="int",
        v=0.0,
        min=0.0,
        max=10.0,
        step=3.0,
        delta=1.0,
        comment="",
        not_used=False,
    )
    # Near 4 should snap to nearest integer
    assert quantize_value(p, 3.6) == 4.0
    # Above 10 clamps to 10
    assert quantize_value(p, 11.0) == 10.0


def test_compute_variant_id_sorted_and_types() -> None:
    params = [
        ParamEntry("b", "float", 1.23456, 0.0, 10.0, 0.1, 1.0, "", False),
        ParamEntry("a", "int", 4.7, 0.0, 10.0, 1.0, 1.0, "", False),
    ]
    vid1 = compute_variant_id_from_entries(params, sort_by_name=True)
    # Reorder should yield the same variant id when sorted
    params2 = list(reversed(params))
    vid2 = compute_variant_id_from_entries(params2, sort_by_name=True)
    assert vid1 == vid2
