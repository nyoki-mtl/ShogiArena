import pytest

from shogiarena._core.contexts.match.ports.usi_think_ports import UsiThinkRequest, request_from_time_controls
from shogiarena._core.shared.kernel.time_control import GameClock, TimeControlLimits


def test_fixed_time_go_command_and_timeout():
    limits = TimeControlLimits(fixed_time_ms=1000, expiry_margin_ms=100)
    tc = GameClock(limits)
    enemy = GameClock(limits)

    request_black = request_from_time_controls(
        my_limits=tc.limits,
        enemy_limits=enemy.limits,
        is_my_black=True,
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
    tc_black = GameClock(limits)
    tc_white = GameClock(limits)
    tc_black.initialize_for_game()
    tc_white.initialize_for_game()

    request_black = request_from_time_controls(
        my_limits=tc_black.limits,
        enemy_limits=tc_white.limits,
        is_my_black=True,
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
        is_my_black=False,
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
    my_tc = GameClock(my_limits)
    enemy_tc = GameClock(enemy_limits)
    my_tc.initialize_for_game()
    enemy_tc.initialize_for_game()

    req = request_from_time_controls(
        my_limits=my_tc.limits,
        enemy_limits=enemy_tc.limits,
        is_my_black=True,
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
    my_tc = GameClock(my_limits)
    enemy_tc = GameClock(enemy_limits)
    my_tc.initialize_for_game()
    enemy_tc.initialize_for_game()

    req = request_from_time_controls(
        my_limits=my_tc.limits,
        enemy_limits=enemy_tc.limits,
        is_my_black=False,
        my_remaining_ms=my_tc.active_time_left_ms(),
        enemy_remaining_ms=enemy_tc.active_time_left_ms(),
    )
    assert req.btime == 45000
    assert req.wtime == 60000
    assert req.byoyomi == 5000


def test_byoyomi_think_request_subtracts_enemy_increment_like_shogihome() -> None:
    # Mirrors shogihome buildTimeState behavior:
    # when my side uses byoyomi and enemy uses increment, enemy main time is
    # advertised as (remaining - increment) while sending only byoyomi.
    my_limits = TimeControlLimits(time_ms=37082, byoyomi_ms=10000)
    enemy_limits = TimeControlLimits(time_ms=28103, increment_ms=5000)
    req = request_from_time_controls(
        my_limits=my_limits,
        enemy_limits=enemy_limits,
        is_my_black=True,
        my_remaining_ms=37082,
        enemy_remaining_ms=28103,
    )
    assert req.btime == 37082
    assert req.wtime == 23103
    assert req.byoyomi == 10000
    assert req.binc is None
    assert req.winc is None


def test_search_limits_think_request() -> None:
    my_limits = TimeControlLimits(depth_limit=16, node_limit=2000)
    enemy_limits = TimeControlLimits(depth_limit=12)
    my_tc = GameClock(my_limits)
    enemy_tc = GameClock(enemy_limits)

    req = request_from_time_controls(
        my_limits=my_tc.limits,
        enemy_limits=enemy_tc.limits,
        is_my_black=True,
        my_remaining_ms=my_tc.active_time_left_ms(),
        enemy_remaining_ms=enemy_tc.active_time_left_ms(),
    )
    assert req.depth == 16
    assert req.nodes == 2000
    # no movetime/time fields expected
    assert req.movetime is None
    assert req.btime is None


def test_time_control_validation_errors():
    import pytest

    with pytest.raises(ValueError):
        GameClock(TimeControlLimits(fixed_time_ms=0))
    with pytest.raises(ValueError):
        GameClock(TimeControlLimits(increment_ms=1000))  # missing time_ms
    with pytest.raises(ValueError):
        GameClock(TimeControlLimits(byoyomi_ms=1000))  # missing time_ms
    with pytest.raises(ValueError):
        GameClock(TimeControlLimits(time_ms=1000, expiry_margin_ms=-1))


def test_time_control_rejects_depth_alias_with_actionable_hint() -> None:
    with pytest.raises(ValueError, match="depth.*depth_limit"):
        TimeControlLimits.model_validate(
            {
                "depth": 9,
                "expiry_margin_ms": 500,
                "max_wait_ms": 600_000,
            }
        )


def test_time_control_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="unexpected"):
        TimeControlLimits.model_validate({"time_ms": 1000, "unexpected": True})


def test_allow_timeout_uses_finite_cap():
    # Soft overtime returns a finite timeout (min(base, max_wait))
    limits = TimeControlLimits(
        time_ms=1000,
        increment_ms=0,
        expiry_margin_ms=0,
        should_allow_timeout=True,
        max_wait_ms=2000,
    )
    tc = GameClock(limits)
    tc.initialize_for_game()
    t = tc.get_timeout_for_wait()
    assert t is not None
    assert pytest.approx(t, rel=1e-3) == 2.0


def test_search_limits_default_wait_and_cap():
    import pytest

    # Default generous wait for search-only
    limits = TimeControlLimits(depth_limit=10)
    tc = GameClock(limits)
    t = tc.get_timeout_for_wait()
    assert pytest.approx(t, rel=1e-3) == 600.0  # 10 minutes

    # With should_allow_timeout + smaller cap, use cap (120s)
    limits2 = TimeControlLimits(depth_limit=10, should_allow_timeout=True, max_wait_ms=120_000)
    tc2 = GameClock(limits2)
    t2 = tc2.get_timeout_for_wait()
    assert pytest.approx(t2, rel=1e-3) == 120.0


def test_build_time_control_limits_falsy_max_wait_does_not_crash():
    # Regression: a falsy max_wait_ms (0) previously triggered ``TimeControlLimits.max_wait_ms``,
    # which raises AttributeError under Pydantic v2 (model fields are not class attributes).
    from shogiarena._core.shared.kernel.time_control_resolution import build_time_control_limits

    base = TimeControlLimits(time_ms=1000, max_wait_ms=0)
    resolved = build_time_control_limits(base, None)
    assert resolved is not None
    assert resolved.max_wait_ms == 0


def test_build_time_control_limits_defaults_max_wait_when_unset():
    from shogiarena._core.shared.kernel.time_control import DEFAULT_MAX_WAIT_MS
    from shogiarena._core.shared.kernel.time_control_resolution import build_time_control_limits

    base = TimeControlLimits(time_ms=1000)
    resolved = build_time_control_limits(base, None)
    assert resolved is not None
    assert resolved.max_wait_ms == DEFAULT_MAX_WAIT_MS
