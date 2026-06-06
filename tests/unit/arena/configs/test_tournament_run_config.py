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


def test_from_mapping_accepts_usi_transcript_logging_section(tmp_path: Path) -> None:
    payload = _minimal_tournament_mapping()
    payload["logging"] = {
        "usi_transcript": True,
        "usi_transcript_detail": "commands_and_info",
    }

    cfg = TournamentRunConfig.from_mapping(payload, base_dir=tmp_path)

    assert cfg.logging.is_usi_transcript_enabled is True
    assert cfg.logging.usi_transcript_detail == "commands_and_info"


def test_from_mapping_rejects_generate_and_tournament_sections(tmp_path: Path) -> None:
    payload = _minimal_tournament_mapping()
    payload["generate"] = {"games": 100, "seed": 42, "num_parallel": 4}

    with pytest.raises(ValueError, match="Generate config must not include tournament section"):
        TournamentRunConfig.from_mapping(payload, base_dir=tmp_path)


def test_path_preflight_error_rejects_missing_builtin_path_option(tmp_path: Path) -> None:
    engine_binary = tmp_path / "engine-a"
    engine_binary.write_text("#!/bin/sh\n", encoding="utf-8")
    engine_config = tmp_path / "engine-a.yaml"
    engine_config.write_text(
        """
name: engine-a
engine_path: "{engine_path}"
options:
  EvalDir: "{missing_eval}"
        """.format(
            engine_path=engine_binary,
            missing_eval=tmp_path / "missing-eval",
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    payload = {
        "experiment_name": "tournament",
        "engines": [
            {"name": "dev", "engine_path": str(engine_config)},
            {"name": "base", "artifact": "YaneuraOu/eb2856f9"},
        ],
        "rules": {},
        "system": {"path_preflight": "error"},
    }

    with pytest.raises(FileNotFoundError, match="EvalDir"):
        TournamentRunConfig.from_mapping(payload, base_dir=tmp_path)


def test_path_preflight_warn_allows_missing_custom_path_option(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = _minimal_tournament_mapping()
    payload["system"] = {"path_preflight": "warn"}
    payload["engines"] = [
        {
            "name": "dev",
            "artifact": "YaneuraOu/a5ee2786",
            "path_options": ["NetworkFile"],
            "options": {"NetworkFile": str(tmp_path / "missing-network.bin")},
        },
        {"name": "base", "artifact": "YaneuraOu/eb2856f9"},
    ]

    with caplog.at_level("WARNING"):
        cfg = TournamentRunConfig.from_mapping(payload, base_dir=tmp_path)

    assert cfg.system.path_preflight == "warn"
    assert "NetworkFile" in caplog.text
