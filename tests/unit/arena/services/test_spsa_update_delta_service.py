from __future__ import annotations

from shogiarena._core.contexts.game_session.application.orchestration.update_delta_service import (
    SpsaUpdateDeltaRequest,
    SpsaUpdateDeltaService,
)
from shogiarena._core.contexts.spsa.domain.spsa_models import ParamEntry


def _param(name: str, value: float, *, delta: float = 0.5, is_not_used: bool = False) -> ParamEntry:
    return ParamEntry(name, "float", value, -10.0, 10.0, 0.5, delta, "", is_not_used)


def test_apply_updates_gradients_and_deltas() -> None:
    service = SpsaUpdateDeltaService()
    params = [
        _param("a", 1.0, delta=0.5),
        _param("b", 2.0, delta=0.5, is_not_used=True),
    ]

    response = service.apply(
        request=SpsaUpdateDeltaRequest(
            params=params,
            perturbations=[1.0, -1.0],
            step=2.0,
            step_factor=1.0,
            c_k=1.0,
            mobility_factor=0.5,
            early_stop_delta_norm_threshold=None,
        ),
        quantize_value=lambda _entry, value: value,
    )

    assert response.gradients["a"] == 1.0
    assert response.deltas["a"] == 0.25
    assert response.delta_norm == 0.25
    assert response.changed_params == 1
    assert response.should_stop is False
    assert params[0].value == 1.25
    assert params[1].value == 2.0


def test_apply_marks_should_stop_on_small_delta_norm() -> None:
    service = SpsaUpdateDeltaService()
    params = [_param("a", 1.0, delta=0.0)]

    response = service.apply(
        request=SpsaUpdateDeltaRequest(
            params=params,
            perturbations=[1.0],
            step=0.0,
            step_factor=0.0,
            c_k=1.0,
            mobility_factor=1.0,
            early_stop_delta_norm_threshold=0.1,
        ),
        quantize_value=lambda _entry, value: value,
    )

    assert response.delta_norm == 0.0
    assert response.changed_params == 0
    assert response.should_stop is True
