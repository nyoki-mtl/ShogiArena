from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from examples.bootstrap_yaneuraou import (
    MACOS_ARM64_PLAN,
    MACOS_X64_PLAN,
    WINDOWS_X64_PLAN,
    _write_configs,
    _write_search_spsa_configs,
    select_prebuilt_plan,
)

from shogiarena._core.interfaces.cli.config_file_loaders import (
    parse_spsa_config_file,
    parse_tournament_config_file,
)
from shogiarena._core.interfaces.cli.run.spsa import _validate_spsa_input_files


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Windows", "AMD64", WINDOWS_X64_PLAN),
        ("Darwin", "arm64", MACOS_ARM64_PLAN),
        ("Darwin", "x86_64", MACOS_X64_PLAN),
        ("Linux", "x86_64", None),
        ("Linux", "aarch64", None),
    ],
)
def test_select_prebuilt_plan(system: str, machine: str, expected: object) -> None:
    assert select_prebuilt_plan(system, machine) == expected


def test_select_prebuilt_plan_rejects_unsupported_platform() -> None:
    with pytest.raises(RuntimeError, match="unsupported platform"):
        select_prebuilt_plan("Plan9", "mips")


def test_generated_tournament_config_passes_public_boundary(tmp_path: Path) -> None:
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    engine_binary = engine_dir / "YaneuraOu"
    engine_binary.write_bytes(b"engine")

    engine_config, tournament_config, spsa_config, spsa_space = _write_configs(tmp_path, engine_binary)
    payload = parse_tournament_config_file(tournament_config)

    assert engine_config.is_file()
    assert yaml.safe_load(engine_config.read_text(encoding="utf-8"))["options"]["Threads"] == 2
    spsa_engine_payload = yaml.safe_load((tmp_path / "spsa-engine.yaml").read_text(encoding="utf-8"))
    assert spsa_engine_payload["options"]["Threads"] == 2
    assert "FV_SCALE" not in spsa_engine_payload["options"]
    assert (tmp_path / "openings.sfen").read_text(encoding="utf-8").count("\n") == 3
    assert payload["experiment_name"] == "yaneuraou-suisho5-quickstart"
    assert payload["tournament"] == {
        "scheduler": "round_robin",
        "games_per_pair": 4,
        "num_parallel": 2,
        "engine_lifecycle": "per_game",
    }
    assert payload["rules"]["initial_positions"] == {
        "type": "file",
        "source": "openings.sfen",
        "source_format": "sfen",
        "flip_policy": "pair_both",
        "sync_scope": "pair",
    }
    assert spsa_config.is_file()
    assert spsa_space.is_file()


def test_generated_spsa_config_targets_yaneuraou_spin_option(tmp_path: Path) -> None:
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    engine_binary = engine_dir / "YaneuraOu"
    engine_binary.write_bytes(b"engine")

    _engine_config, _tournament_config, spsa_config, spsa_space = _write_configs(tmp_path, engine_binary)
    boundary_payload = parse_spsa_config_file(spsa_config)
    config_payload = yaml.safe_load(spsa_config.read_text(encoding="utf-8"))
    space_payload = yaml.safe_load(spsa_space.read_text(encoding="utf-8"))

    assert config_payload["spsa"]["num_updates"] == 2
    assert config_payload["spsa"]["variants"]["apply"] == {
        "clear_hash": False,
        "after_setoption": "isready",
    }
    assert config_payload["system"]["engine_handshake_timeout"] == 5
    assert boundary_payload["experiment_name"] == "yaneuraou-suisho5-spsa-quickstart"
    assert space_payload["target"]["tunable_manifest"]["required"] is False
    assert space_payload["parameters"][0]["target"]["option"] == "FV_SCALE"


def test_generated_search_spsa_config_requires_fork_manifest_and_clear_hash(tmp_path: Path) -> None:
    engine_dir = tmp_path / "search-spsa-engine"
    engine_dir.mkdir()
    engine_binary = engine_dir / "YaneuraOu"
    engine_binary.write_bytes(b"engine")
    (tmp_path / "openings.sfen").write_text("startpos\n", encoding="utf-8")

    engine_config, spsa_config, spsa_space = _write_search_spsa_configs(tmp_path, engine_binary)
    boundary_payload = parse_spsa_config_file(spsa_config)
    engine_payload = yaml.safe_load(engine_config.read_text(encoding="utf-8"))
    config_payload = yaml.safe_load(spsa_config.read_text(encoding="utf-8"))
    space_payload = yaml.safe_load(spsa_space.read_text(encoding="utf-8"))

    assert engine_payload["options"]["Threads"] == 2
    assert boundary_payload["experiment_name"] == "yaneuraou-v940-search-spsa"
    assert config_payload["spsa"]["variants"]["apply"] == {
        "clear_hash": True,
        "after_setoption": "isready",
    }
    assert config_payload["spsa"]["pairs_per_update"] == 2
    assert config_payload["spsa"]["num_parallel"] == 4
    assert config_payload["rules"]["adjudication"]["max_plies"] == 320
    assert space_payload["target"]["tunable_manifest"]["required"] is True
    assert "parameters" not in space_payload
    assert space_payload["select"] == [
        "aspiration_window_1",
        "aspiration_window_2",
        "lowPlyHistory_fill_1",
        "correction_value_1",
        "correction_value_2",
        "update_correction_history1_1",
        "Search_correction_history_bonus_1",
        "YaneuraOuWorker_clear2_1",
        "YaneuraOuWorker_clear3_1",
        "Search_tt_lookup1_1",
        "Search_tt_lookup1_2",
        "Search_tt_lookup2_1",
        "Search_static_evaluation_1a_1",
        "Search_static_evaluation_1a_2",
        "Search_razoring_1",
        "Search_futility_1_1",
        "Search_futility_1_3",
        "Search_nullmove_1_1",
        "Search_nullmove_1_2",
        "Search_Probcut_1",
        "Search_Probcut_2",
        "Search_small_Probcut_1",
        "Search_Continuation_history_based_pruning1_2",
        "Search_Extensions1_1",
        "Search_Extensions3_2",
        "YaneuraOuWorker_reduction_1",
        "YaneuraOuWorker_reduction_2",
        "Search_LMR_research_thresholds_1",
        "Search_Capture_SEE_pruning_margin_1",
        "QSearch_SEE_pruning_1",
        "MovePicker_good_capture_see_1",
        "MovePicker_quiet_partial_sort_1",
    ]
    _validate_spsa_input_files(
        SimpleNamespace(
            space_path=spsa_space,
            start_sfens_path=tmp_path / "openings.sfen",
        )
    )
