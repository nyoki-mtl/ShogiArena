from __future__ import annotations

from pathlib import Path

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.config_tournament import TournamentRunConfig


def _minimal_tournament_mapping() -> dict[str, object]:
    return {
        "experiment_name": "tournament",
        "engines": [
            {"name": "dev", "artifact": "YaneuraOu/a5ee2786"},
            {"name": "base", "artifact": "YaneuraOu/eb2856f9"},
        ],
        "rules": {},
        "tournament": {
            "scheduler": "gauntlet",
            "games_per_pair": 10,
            "seed": 42,
            "num_parallel": 4,
            "baseline_count": 1,
        },
    }


def test_from_mapping_accepts_null_generate_with_tournament(tmp_path: Path) -> None:
    payload = _minimal_tournament_mapping()
    payload["generate"] = None

    cfg = TournamentRunConfig.from_mapping(payload, base_dir=tmp_path)

    assert cfg.generate is None
    assert cfg.tournament.scheduler == "gauntlet"


def test_from_mapping_rejects_generate_and_tournament_sections(tmp_path: Path) -> None:
    payload = _minimal_tournament_mapping()
    payload["generate"] = {"games": 100, "seed": 42, "num_parallel": 4}

    with pytest.raises(ValueError, match="Generate config must not include tournament section"):
        TournamentRunConfig.from_mapping(payload, base_dir=tmp_path)
