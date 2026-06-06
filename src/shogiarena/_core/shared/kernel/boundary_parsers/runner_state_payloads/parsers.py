from __future__ import annotations

from collections.abc import Mapping

from shogiarena._core.shared.kernel.boundary_parsers.openbench import parse_openbench_client_state_boundary
from shogiarena._core.shared.kernel.contracts import parse_wire
from shogiarena._core.shared.kernel.json_types import JsonValue

from .state_models import (
    _BOUNDARY_ID_SPSA,
    _BOUNDARY_ID_SPSA_INDEX,
    _BOUNDARY_ID_TOURNAMENT,
    _RunStatePayload,
    _SpsaIndexPayload,
    _SpsaRunStatePayload,
    _TournamentRunStateType,
)


def parse_tournament_run_state_boundary(
    payload: Mapping[str, object],
    *,
    path: str = "root",
) -> _TournamentRunStateType:
    """Validate and parse tournament run_state persistence payload."""
    wire_payload = parse_wire(
        boundary_id=_BOUNDARY_ID_TOURNAMENT,
        payload=payload,
        model=_RunStatePayload,
        path=path,
    )
    parsed_state = wire_payload.model_dump(mode="python")

    openbench_state = parsed_state.get("openbench_state")
    if openbench_state is not None:
        parsed_state["openbench_state"] = parse_openbench_client_state_boundary(
            openbench_state,
            path=f"{path}.openbench_state",
        )

    typed_state: _TournamentRunStateType = parsed_state
    return typed_state


def parse_spsa_index_boundary(payload: Mapping[str, object], *, path: str = "root") -> dict[str, JsonValue]:
    """Validate and parse SPSA index.json persistence payload."""
    wire_payload = parse_wire(
        boundary_id=_BOUNDARY_ID_SPSA_INDEX,
        payload=payload,
        model=_SpsaIndexPayload,
        path=path,
    )
    parsed = wire_payload.model_dump(mode="python")
    if not isinstance(parsed, dict):
        return {}
    return {str(key): value for key, value in parsed.items()}


def parse_spsa_run_state_boundary(payload: Mapping[str, object], *, path: str = "root") -> dict[str, JsonValue]:
    """Validate and parse SPSA run_state persistence payload."""
    wire_payload = parse_wire(
        boundary_id=_BOUNDARY_ID_SPSA,
        payload=payload,
        model=_SpsaRunStatePayload,
        path=path,
    )
    return wire_payload.model_dump(mode="python", by_alias=True)


__all__ = [
    "parse_spsa_index_boundary",
    "parse_spsa_run_state_boundary",
    "parse_tournament_run_state_boundary",
]
