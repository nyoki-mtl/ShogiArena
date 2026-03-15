from __future__ import annotations

from shogiarena._core.contexts.game_session.application.orchestration.context_service import (
    SpsaOrchestrationContextRequest,
    build_orchestration_context,
)


def test_build_resolves_ltc_labels_and_side_mapping() -> None:
    context = build_orchestration_context(
        request=SpsaOrchestrationContextRequest(
            tuned_token="v000123",
            baseline_token="v000120",
            is_tuned_as_black=True,
            tuned_engine_name="tuned-engine",
            baseline_engine_name="base-engine",
            phase="ltc",
            phase_suffix="",
        )
    )

    assert context.tuned_label == "v000123-tuned"
    assert context.baseline_label == "v000120-base"
    assert context.variant_label == "v000123"
    assert context.black_engine_name == "tuned-engine"
    assert context.white_engine_name == "base-engine"
    assert context.black_player_label == "v000123-tuned"
    assert context.white_player_label == "v000120-base"


def test_build_resolves_non_ltc_labels_and_swapped_sides() -> None:
    context = build_orchestration_context(
        request=SpsaOrchestrationContextRequest(
            tuned_token="v000124",
            baseline_token="v000124",
            is_tuned_as_black=False,
            tuned_engine_name="tuned-engine",
            baseline_engine_name="base-engine",
            phase="minus",
            phase_suffix="-",
        )
    )

    assert context.tuned_label == "v000124-minus"
    assert context.baseline_label == "v000124-base"
    assert context.variant_label == "v000124-"
    assert context.black_engine_name == "base-engine"
    assert context.white_engine_name == "tuned-engine"
    assert context.black_player_label == "v000124-base"
    assert context.white_player_label == "v000124-minus"
