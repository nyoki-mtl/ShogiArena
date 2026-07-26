from __future__ import annotations

from shogiarena._core.interfaces.dashboard.ws_server import LiveWebSocketHub


def _hub() -> LiveWebSocketHub:
    return LiveWebSocketHub()


def test_no_subscribers_means_no_engine_io_demand() -> None:
    hub = _hub()
    assert hub.has_engine_io_subscribers("g1") is False


def test_per_game_io_topic_subscriber_creates_demand() -> None:
    hub = _hub()
    hub._topic_subscribers["live.engine.g1.black.io.diff"] = 1  # noqa: SLF001
    assert hub.has_engine_io_subscribers("g1") is True
    # Demand is scoped to the game: another game is unaffected.
    assert hub.has_engine_io_subscribers("g2") is False


def test_io_snapshot_topic_also_creates_demand() -> None:
    hub = _hub()
    hub._topic_subscribers["live.engine.g1.white.io.snapshot"] = 1  # noqa: SLF001
    assert hub.has_engine_io_subscribers("g1") is True


def test_global_subscriber_creates_demand_for_every_game() -> None:
    hub = _hub()
    hub._global_subscribers = 1  # noqa: SLF001
    assert hub.has_engine_io_subscribers("g1") is True
    assert hub.has_engine_io_subscribers("anything") is True


def test_unrelated_topic_subscriber_does_not_create_io_demand() -> None:
    hub = _hub()
    hub._topic_subscribers["live.game.g1.moves.diff"] = 3  # noqa: SLF001
    assert hub.has_engine_io_subscribers("g1") is False
