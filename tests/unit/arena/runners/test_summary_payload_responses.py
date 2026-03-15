from __future__ import annotations

from shogiarena._core.contexts.game_session.application.summary.dashboard_sections_service import (
    TournamentSummaryDashboardSectionsResponse,
)
from shogiarena._core.contexts.game_session.application.summary.optional_sections_service import (
    TournamentSummaryOptionalSectionsResponse,
)
from shogiarena._core.contexts.game_session.application.summary.payload_responses import (
    TournamentDashboardSummaryPayloadResponse,
    TournamentSeedSummaryPayloadResponse,
)


def test_seed_summary_payload_response_applies_optional_sections() -> None:
    response = TournamentSeedSummaryPayloadResponse(
        base_payload={
            "mode": "tournament",
            "flipPolicy": "",
        },
        optional_sections=TournamentSummaryOptionalSectionsResponse(
            rules={"repetition": 4},
            initial_positions=["startpos"],
            repetition_occurrences_to_draw=4,
            flip_policy="balanced",
            tournament_config={"scheduler": "round_robin"},
            sprt={"elo0": 0.0},
            generate_config={"games": 2},
            records_output={"format": "csa"},
        ),
    )

    payload = response.to_json_object()

    assert payload["rules"] == {"repetition": 4}
    assert payload["initialPositions"] == ["startpos"]
    assert payload["repetitionOccurrencesToDraw"] == 4
    assert payload["flipPolicy"] == "balanced"
    assert payload["tournamentConfig"] == {"scheduler": "round_robin"}
    assert payload["sprt"] == {"elo0": 0.0}
    assert payload["generateConfig"] == {"games": 2}
    assert payload["recordsOutput"] == {"format": "csa"}


def test_dashboard_summary_payload_response_merges_dashboard_sections() -> None:
    response = TournamentDashboardSummaryPayloadResponse(
        base_payload={"mode": "tournament"},
        optional_sections=TournamentSummaryOptionalSectionsResponse(
            rules={"repetition": 4},
            initial_positions=None,
            repetition_occurrences_to_draw=None,
            flip_policy=None,
            tournament_config=None,
            sprt=None,
            generate_config=None,
            records_output=None,
        ),
        dashboard_sections=TournamentSummaryDashboardSectionsResponse(
            btd={"ratings": {"e1": {"elo": 1.0}}},
            records_summary={"games": 2},
            pentanomial={"pairs": 1},
            sprt={"llr": 0.5},
            pair_outcome={"wins_a": 1, "wins_b": 0, "draws": 1},
        ),
    )

    payload = response.to_json_object()

    assert payload["btd"] == {"ratings": {"e1": {"elo": 1.0}}}
    assert payload["recordsSummary"] == {"games": 2}
    assert payload["pentanomial"] == {"pairs": 1}
    assert payload["sprt"] == {"llr": 0.5}
    assert payload["wins_a"] == 1
    assert payload["wins_b"] == 0
    assert payload["draws"] == 1
