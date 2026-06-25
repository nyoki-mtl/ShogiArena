from shogiarena._core.platform.engine_runtime.usi_protocol_types import (
    UsiBound,
    UsiEvalValue,
    UsiThinkPV,
    UsiThinkResult,
)


def test_eval_value_to_string_variants() -> None:
    assert UsiEvalValue(17).to_string() == "cp 17"
    # Mate in N
    assert UsiEvalValue.mate_in_ply(3).to_string() == "mate 3"
    # Mated in N (negative)
    assert UsiEvalValue.mated_in_ply(2).to_string() == "mate -2"
    assert UsiEvalValue.mated_in_ply(0).to_string() == "mate -0"


def test_eval_value_structured_accessors() -> None:
    cp = UsiEvalValue(17)
    assert cp.kind == "cp"
    assert cp.value == 17
    assert cp.is_cp
    assert not cp.is_mate
    assert cp.as_dict() == {"kind": "cp", "value": 17}

    mate = UsiEvalValue.mated_in_ply(2)
    assert mate.kind == "mate"
    assert mate.value == -2
    assert mate.is_mate
    assert not mate.is_cp
    assert mate.as_dict() == {"kind": "mate", "value": -2}


def test_bound_to_from_string_including_exact() -> None:
    assert UsiBound.from_string("upperbound") is UsiBound.UPPER
    assert UsiBound.from_string("lowerbound") is UsiBound.LOWER
    assert UsiBound.from_string("exact") is UsiBound.EXACT
    assert UsiBound.from_string("unknown") is UsiBound.NONE
    assert UsiBound.UPPER.to_string() == "upperbound"
    assert UsiBound.LOWER.to_string() == "lowerbound"
    # EXACT and NONE do not serialize to a token
    assert UsiBound.EXACT.to_string() == ""
    assert UsiBound.NONE.to_string() == ""


def test_from_info_string_metrics_only_returns_object() -> None:
    # depth only (no score/pv), should still yield an object
    pv = UsiThinkPV.from_info_string("info depth 12")
    assert pv is not None
    assert pv.depth == 12
    # nodes + time + nps
    pv2 = UsiThinkPV.from_info_string("info nodes 12345 time 67 nps 89000")
    assert pv2 is not None
    assert pv2.nodes == 12345 and pv2.time == 67 and pv2.nps == 89000


def test_from_info_string_parses_score_and_bound() -> None:
    pv = UsiThinkPV.from_info_string("info score cp 34 upperbound")
    assert pv is not None and pv.eval == 34 and pv.bound is UsiBound.UPPER
    assert pv.bound_name == "upperbound"
    pv2 = UsiThinkPV.from_info_string("info score mate -3 lowerbound")
    assert pv2 is not None and isinstance(pv2.eval, UsiEvalValue) and pv2.bound is UsiBound.LOWER
    assert pv2.eval.to_string() == "mate -3"
    assert pv2.bound_name == "lowerbound"


def test_get_last_pv_works_with_default_index() -> None:
    r = UsiThinkResult()
    a = UsiThinkPV()
    a.multipv = 1
    a.depth = 10
    b = UsiThinkPV()
    b.multipv = 2
    b.depth = 12
    c = UsiThinkPV()
    c.multipv = 1
    c.depth = 14
    r.pvs.extend([a, b, c])
    last = r.get_last_pv()
    assert last is c and last.depth == 14


def test_select_pv_prefers_exact_depth_then_deepest_fallback() -> None:
    r = UsiThinkResult()
    a = UsiThinkPV()
    a.multipv = 1
    a.depth = 10
    b = UsiThinkPV()
    b.multipv = 1
    b.depth = 12
    c = UsiThinkPV()
    c.multipv = 1
    c.depth = 12
    d = UsiThinkPV()
    d.multipv = 2
    d.depth = 20
    r.pvs.extend([a, b, c, d])

    assert r.select_pv(depth=12, multipv=1) is c
    assert r.select_pv(depth=99, multipv=1) is c
    assert r.select_pv(depth=99, multipv=1, fallback="last") is c
    assert r.select_pv(depth=99, multipv=1, fallback="none") is None
    assert r.select_pv(depth=20, multipv=2) is d
