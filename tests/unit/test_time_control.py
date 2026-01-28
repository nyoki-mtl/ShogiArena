import pytest

from shogiarena.arena.engines.time_control import TimeControl, TimeControlLimits
from shogiarena.arena.engines.usi_think import UsiThinkRequest, request_from_time_controls


def test_fixed_time_go_command_and_timeout():
    limits = TimeControlLimits(fixed_time_ms=1000, expiry_margin_ms=100)
    tc = TimeControl(limits)
    enemy = TimeControl(limits)

    request_black = request_from_time_controls(
        my_limits=tc.limits,
        enemy_limits=enemy.limits,
        my_is_black=True,
        my_remaining_ms=tc.active_time_left_ms(),
        enemy_remaining_ms=enemy.active_time_left_ms(),
    )
    assert isinstance(request_black, UsiThinkRequest)
    assert request_black.movetime == 1000

    timeout = tc.get_timeout_for_wait()
    # fixed + margin + 1000ms buffer = 2100ms => 2.1s
    assert pytest.approx(timeout, rel=1e-3) == 2.1


def test_time_increment_go_command_includes_times_and_increments():
    limits = TimeControlLimits(time_ms=60000, increment_ms=2000, expiry_margin_ms=100)
    tc_black = TimeControl(limits)
    tc_white = TimeControl(limits)
    tc_black.initialize_for_game()
    tc_white.initialize_for_game()

    request_black = request_from_time_controls(
        my_limits=tc_black.limits,
        enemy_limits=tc_white.limits,
        my_is_black=True,
        my_remaining_ms=tc_black.active_time_left_ms(),
        enemy_remaining_ms=tc_white.active_time_left_ms(),
    )
    # In Arena's Fischer handling, advertised time subtracts increment
    assert request_black.btime == 58000
    assert request_black.wtime == 58000
    assert request_black.binc == 2000
    assert request_black.winc == 2000

    request_white = request_from_time_controls(
        my_limits=tc_white.limits,
        enemy_limits=tc_black.limits,
        my_is_black=False,
        my_remaining_ms=tc_white.active_time_left_ms(),
        enemy_remaining_ms=tc_black.active_time_left_ms(),
    )
    assert request_white.btime == 58000
    assert request_white.wtime == 58000
    assert request_white.binc == 2000
    assert request_white.winc == 2000


def test_time_increment_uses_opponent_increment() -> None:
    my_limits = TimeControlLimits(time_ms=60000, increment_ms=1000)
    enemy_limits = TimeControlLimits(time_ms=60000, increment_ms=2000)
    my_tc = TimeControl(my_limits)
    enemy_tc = TimeControl(enemy_limits)
    my_tc.initialize_for_game()
    enemy_tc.initialize_for_game()

    req = request_from_time_controls(
        my_limits=my_tc.limits,
        enemy_limits=enemy_tc.limits,
        my_is_black=True,
        my_remaining_ms=my_tc.active_time_left_ms(),
        enemy_remaining_ms=enemy_tc.active_time_left_ms(),
    )
    assert req.btime == 59000  # subtract own increment from advertised time
    assert req.wtime == 58000  # subtract enemy increment from advertised time
    assert req.binc == 1000
    assert req.winc == 2000


def test_byoyomi_think_request() -> None:
    my_limits = TimeControlLimits(time_ms=60000, byoyomi_ms=5000)
    enemy_limits = TimeControlLimits(time_ms=45000, byoyomi_ms=2000)
    my_tc = TimeControl(my_limits)
    enemy_tc = TimeControl(enemy_limits)
    my_tc.initialize_for_game()
    enemy_tc.initialize_for_game()

    req = request_from_time_controls(
        my_limits=my_tc.limits,
        enemy_limits=enemy_tc.limits,
        my_is_black=False,
        my_remaining_ms=my_tc.active_time_left_ms(),
        enemy_remaining_ms=enemy_tc.active_time_left_ms(),
    )
    assert req.btime == 45000
    assert req.wtime == 60000
    assert req.byoyomi == 5000


def test_search_limits_think_request() -> None:
    my_limits = TimeControlLimits(depth_limit=16, node_limit=2000)
    enemy_limits = TimeControlLimits(depth_limit=12)
    my_tc = TimeControl(my_limits)
    enemy_tc = TimeControl(enemy_limits)

    req = request_from_time_controls(
        my_limits=my_tc.limits,
        enemy_limits=enemy_tc.limits,
        my_is_black=True,
        my_remaining_ms=my_tc.active_time_left_ms(),
        enemy_remaining_ms=enemy_tc.active_time_left_ms(),
    )
    assert req.depth == 16
    assert req.nodes == 2000
    # no movetime/time fields expected
    assert req.movetime is None
    assert req.btime is None


def test_time_control_spec_roundtrip_ms_precision():
    # ms precision preserved for time/increment/byoyomi and suffixes
    limits = TimeControlLimits(
        time_ms=1500,
        increment_ms=500,
        byoyomi_ms=None,
        fixed_time_ms=None,
        depth_limit=10,
        node_limit=1000,
        expiry_margin_ms=250,
        allow_timeout=True,
        max_wait_ms=12345,
    )
    s = limits.to_spec_str()
    parsed = TimeControlLimits.from_spec_str(s)
    assert parsed.time_ms == 1500
    assert parsed.increment_ms == 500
    assert parsed.byoyomi_ms is None
    assert parsed.fixed_time_ms is None
    assert parsed.depth_limit == 10
    assert parsed.node_limit == 1000
    assert parsed.expiry_margin_ms == 250
    assert parsed.allow_timeout is True
    assert parsed.max_wait_ms == 12345


def test_time_control_validation_errors():
    import pytest

    with pytest.raises(ValueError):
        TimeControl(TimeControlLimits(fixed_time_ms=0))
    with pytest.raises(ValueError):
        TimeControl(TimeControlLimits(increment_ms=1000))  # missing time_ms
    with pytest.raises(ValueError):
        TimeControl(TimeControlLimits(byoyomi_ms=1000))  # missing time_ms
    with pytest.raises(ValueError):
        TimeControl(TimeControlLimits(time_ms=1000, expiry_margin_ms=-1))


def test_allow_timeout_uses_finite_cap():
    # Soft overtime returns a finite timeout (min(base, max_wait))
    limits = TimeControlLimits(time_ms=1000, increment_ms=0, expiry_margin_ms=0, allow_timeout=True, max_wait_ms=2000)
    tc = TimeControl(limits)
    tc.initialize_for_game()
    t = tc.get_timeout_for_wait()
    assert t is not None
    assert pytest.approx(t, rel=1e-3) == 2.0


def test_search_limits_default_wait_and_cap():
    import pytest

    # Default generous wait for search-only
    limits = TimeControlLimits(depth_limit=10)
    tc = TimeControl(limits)
    t = tc.get_timeout_for_wait()
    assert pytest.approx(t, rel=1e-3) == 600.0  # 10 minutes

    # With allow_timeout + smaller cap, use cap (120s)
    limits2 = TimeControlLimits(depth_limit=10, allow_timeout=True, max_wait_ms=120_000)
    tc2 = TimeControl(limits2)
    t2 = tc2.get_timeout_for_wait()
    assert pytest.approx(t2, rel=1e-3) == 120.0
