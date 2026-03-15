"""Build optional tournament summary payload sections from runtime."""

from __future__ import annotations

from dataclasses import dataclass

from shogiarena._core.contexts.game_session.application.summary.runtime_context import TournamentSummaryRuntimeContext
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue


@dataclass(slots=True)
class TournamentSummaryOptionalSectionsResponse:
    """Optional runtime-derived sections for summary payloads."""

    rules: JsonObject | None = None
    initial_positions: JsonValue | None = None
    repetition_occurrences_to_draw: JsonValue | None = None
    flip_policy: JsonValue | None = None
    tournament_config: JsonObject | None = None
    sprt: JsonObject | None = None
    generate_config: dict[str, object] | None = None
    records_output: dict[str, object] | None = None


class TournamentSummaryOptionalSectionsService:
    """Build optional sections shared by seed/update summary payloads."""

    @staticmethod
    def build(runtime: TournamentSummaryRuntimeContext) -> TournamentSummaryOptionalSectionsResponse:
        config = runtime.request.config
        rules_payload = runtime.actions.build_rules_payload()
        if not rules_payload:
            return TournamentSummaryOptionalSectionsResponse()

        initial_positions = rules_payload.get("initial_positions")
        flip_policy = rules_payload.get("flip_policy")
        if not flip_policy and isinstance(initial_positions, dict):
            flip_policy = initial_positions.get("flip_policy")

        sprt_payload = runtime.actions.build_sprt_payload()
        sprt = sprt_payload if sprt_payload else None

        generate_config: dict[str, object] | None = None
        records_output: dict[str, object] | None = None
        if runtime.actions.is_generate_run():
            generate_conf = config.generate
            if generate_conf is not None:
                generate_config = generate_conf.model_dump(mode="json")
            records_output_conf = config.records_output
            if records_output_conf is not None:
                records_output = records_output_conf.model_dump(mode="json")

        return TournamentSummaryOptionalSectionsResponse(
            rules=rules_payload,
            initial_positions=initial_positions,
            repetition_occurrences_to_draw=rules_payload.get("repetition_occurrences_to_draw"),
            flip_policy=flip_policy,
            tournament_config={"rules": rules_payload},
            sprt=sprt,
            generate_config=generate_config,
            records_output=records_output,
        )


__all__ = ["TournamentSummaryOptionalSectionsResponse", "TournamentSummaryOptionalSectionsService"]
