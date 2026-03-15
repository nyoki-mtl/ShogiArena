from __future__ import annotations

from shogiarena._core.contexts.game_session.application.orchestration.request_service import (
    build_dispatch_request,
)


def test_build_creates_dispatch_request_dto() -> None:
    request = build_dispatch_request(
        black_engine_name="black",
        white_engine_name="white",
        black_item_instance_override="inst-a",
        white_item_instance_override="inst-b",
        should_raise_on_missing_instance=True,
    )

    assert request.black_engine_name == "black"
    assert request.white_engine_name == "white"
    assert request.black_item_instance_override == "inst-a"
    assert request.white_item_instance_override == "inst-b"
    assert request.should_raise_on_missing_instance is True
