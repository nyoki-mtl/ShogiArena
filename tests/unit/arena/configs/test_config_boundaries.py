"""Config boundary parser tests for invalid/valid payloads."""

from __future__ import annotations

from pathlib import Path

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.config_core import InitialPositionConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.config_spsa_parser import (
    parse_spsa_config_mapping,
)
from shogiarena._core.interfaces.cli.config_file_loaders import (
    parse_engine_config_file,
    parse_spsa_config_boundary,
    parse_tournament_config_boundary,
)
from shogiarena._core.shared.kernel.exceptions import ContractParseError


def _engine_yaml(path: Path, *, path_suffix: str = "engine.yaml") -> Path:
    engine_file = path / path_suffix
    engine_file.write_text(
        """
        engine_path: "/bin/echo"
        options:
          Threads: 1
        """,
        encoding="utf-8",
    )
    return engine_file


def _minimal_tournament_payload(tmp_path: Path) -> dict[str, object]:
    engine_file = _engine_yaml(tmp_path, path_suffix="tournament-engine.yaml")
    return {
        "engines": [{"engine_path": str(engine_file)}],
        "rules": {},
    }


def _minimal_spsa_payload(tmp_path: Path) -> dict[str, object]:
    start_file = tmp_path / "startpos.txt"
    start_file.write_text("startpos\n", encoding="utf-8")
    space_file = tmp_path / "space.yaml"
    space_file.write_text(
        """
        schema_version: shogiarena.spsa.space.v1
        target:
          engine_family: test
          protocol: usi_options
          required_options_policy: strict
          tunable_manifest:
            required: false
            command: usi_tunables
        parameters:
          - id: p1
            target:
              option: p1
              value_encoding: decimal
            value_type: float
            initial: 1.0
            bounds:
              min: 0.0
              max: 10.0
            schedule:
              c_end: 1.0
              r_end: 0.1
        """,
        encoding="utf-8",
    )
    engine_file = _engine_yaml(tmp_path, path_suffix="spsa-engine.yaml")
    return {
        "engines": [{"engine_path": str(engine_file)}],
        "rules": {
            "initial_positions": {
                "type": "file",
                "source": str(start_file),
            }
        },
        "spsa": {
            "space": str(space_file),
            "num_updates": 1,
        },
    }


def test_parse_tournament_boundary_rejects_unknown_top_level_fields(tmp_path: Path) -> None:
    payload = _minimal_tournament_payload(tmp_path)
    payload["unexpected_field"] = "value"

    with pytest.raises(ContractParseError, match="Failed to parse wire payload"):
        parse_tournament_config_boundary(payload)


def test_parse_tournament_boundary_rejects_invalid_instances_type(tmp_path: Path) -> None:
    payload = _minimal_tournament_payload(tmp_path)
    payload["instances"] = 123

    with pytest.raises(ContractParseError, match="Failed to parse wire payload"):
        parse_tournament_config_boundary(payload)


def test_parse_tournament_boundary_rejects_missing_engines() -> None:
    with pytest.raises(ContractParseError, match="Failed to parse wire payload"):
        parse_tournament_config_boundary({"rules": {}})


def test_parse_tournament_boundary_omits_optional_generate_when_missing(tmp_path: Path) -> None:
    payload = _minimal_tournament_payload(tmp_path)

    parsed = parse_tournament_config_boundary(payload)

    assert "generate" not in parsed


def test_parse_tournament_boundary_omits_optional_generate_when_null(tmp_path: Path) -> None:
    payload = _minimal_tournament_payload(tmp_path)
    payload["generate"] = None

    parsed = parse_tournament_config_boundary(payload)

    assert "generate" not in parsed


def test_parse_spsa_boundary_rejects_missing_required_block(tmp_path: Path) -> None:
    payload = _minimal_spsa_payload(tmp_path)
    payload.pop("spsa")

    with pytest.raises(ContractParseError, match="Failed to parse wire payload"):
        parse_spsa_config_boundary(payload)


def test_parse_spsa_boundary_rejects_invalid_instances_type(tmp_path: Path) -> None:
    payload = _minimal_spsa_payload(tmp_path)
    payload["instances"] = 10

    with pytest.raises(ContractParseError, match="Failed to parse wire payload"):
        parse_spsa_config_boundary(payload)


def test_parse_spsa_boundary_rejects_invalid_rules_payload(tmp_path: Path) -> None:
    payload = _minimal_spsa_payload(tmp_path)
    payload["rules"] = {"initial_positions": {"type": "file"}}

    with pytest.raises(ValueError, match="rules.initial_positions.source is required"):
        parse_spsa_config_boundary(payload)


def test_parse_spsa_boundary_rejects_noncanonical_artifact_id(tmp_path: Path) -> None:
    payload = _minimal_spsa_payload(tmp_path)
    payload["engines"] = [{"artifact": "rshogi-az/local"}]

    with pytest.raises(ValueError, match="artifact must be '<repo>/<commit_hash>'"):
        parse_spsa_config_mapping(payload, source_path=tmp_path / "spsa.yaml")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("inflight_factor", 0, "spsa.inflight_factor"),
        ("num_parallel", 0, "spsa.num_parallel"),
        ("unknown_key", 1, "Unknown keys in spsa"),
    ],
)
def test_parse_spsa_boundary_rejects_unsafe_runtime_values(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _minimal_spsa_payload(tmp_path)
    spsa = payload["spsa"]
    assert isinstance(spsa, dict)
    spsa[field] = value

    with pytest.raises((ContractParseError, ValueError), match=message):
        parse_spsa_config_mapping(payload, source_path=tmp_path / "spsa.yaml")


@pytest.mark.parametrize("field", ["num_updates", "pairs_per_update", "num_parallel", "inflight_factor"])
def test_parse_spsa_boundary_rejects_fractional_integer_values(tmp_path: Path, field: str) -> None:
    payload = _minimal_spsa_payload(tmp_path)
    spsa = payload["spsa"]
    assert isinstance(spsa, dict)
    spsa[field] = 1.9

    with pytest.raises((TypeError, ValueError), match=rf"spsa\.{field}.*integer"):
        parse_spsa_config_mapping(payload, source_path=tmp_path / "spsa.yaml")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("alpha", 0.5),
        ("alpha", 1.1),
        ("gamma", 0.0),
        ("gamma", 0.6),
    ],
)
def test_parse_spsa_boundary_rejects_unsupported_gain_ranges(
    tmp_path: Path,
    field: str,
    value: float,
) -> None:
    payload = _minimal_spsa_payload(tmp_path)
    spsa = payload["spsa"]
    assert isinstance(spsa, dict)
    spsa["algorithm"] = {field: value}

    with pytest.raises((ContractParseError, ValueError), match=field):
        parse_spsa_config_mapping(payload, source_path=tmp_path / "spsa.yaml")


def test_parse_spsa_boundary_rejects_removed_instance_affinity(tmp_path: Path) -> None:
    payload = _minimal_spsa_payload(tmp_path)
    spsa = payload["spsa"]
    assert isinstance(spsa, dict)
    spsa["variants"] = {"instance_affinity": "update"}

    with pytest.raises((ContractParseError, ValueError), match="instance_affinity"):
        parse_spsa_config_mapping(payload, source_path=tmp_path / "spsa.yaml")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("update_mode", "barrier"),
        ("update_mode", "immediate"),
        ("parameters_path", "legacy.params"),
    ],
)
def test_parse_spsa_boundary_rejects_removed_root_keys(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    payload = _minimal_spsa_payload(tmp_path)
    spsa = payload["spsa"]
    assert isinstance(spsa, dict)
    spsa[field] = value

    with pytest.raises((ContractParseError, ValueError), match=field):
        parse_spsa_config_mapping(payload, source_path=tmp_path / "spsa.yaml")


def test_initial_position_config_usi_line_metadata_is_opt_in(tmp_path: Path) -> None:
    source = tmp_path / "lines.usi"
    source.write_text("7g7f 3c3d\n", encoding="utf-8")

    config = InitialPositionConfig(type="file", source=str(source), source_format="usi_line")
    entry = config.generate_entries(1, "seed")[0]

    assert entry.line_moves_usi == ()
    assert entry.source_line_no is None
    assert entry.line_id is None
    assert entry.opening_base_sfen == "startpos"
    assert entry.opening_moves_usi == ("7g7f", "3c3d")


def test_initial_position_config_auto_preserves_sfen_moves_history(tmp_path: Path) -> None:
    base_sfen = "lnsgkgsnl/1r5b1/pppppp1pp/6p2/9/2P6/PP1PPPPPP/1B5R1/LNSGKGSNL b - 3"
    source = tmp_path / "custom.lines"
    source.write_text(f"position sfen {base_sfen} moves 2g2f 8c8d\n", encoding="utf-8")

    config = InitialPositionConfig(type="file", source=str(source), source_format="auto")
    entry = config.generate_entries(1, "seed")[0]

    assert entry.opening_base_sfen == base_sfen
    assert entry.opening_moves_usi == ("2g2f", "8c8d")
    assert entry.initial_sfen.endswith(" b - 5")


def test_initial_position_config_preserves_usi_line_metadata_when_requested(tmp_path: Path) -> None:
    source = tmp_path / "lines.usi"
    source.write_text("7g7f 3c3d\n", encoding="utf-8")

    config = InitialPositionConfig(
        type="file",
        source=str(source),
        source_format="usi_line",
        preserve_line_metadata=True,
    )
    entry = config.generate_entries(1, "seed")[0]

    assert entry.line_moves_usi == ("7g7f", "3c3d")
    assert entry.source_line_no == 1
    assert entry.line_id == "lines.usi:1"


def test_initial_position_config_can_select_without_replacement(tmp_path: Path) -> None:
    source = tmp_path / "lines.usi"
    lines = ["7g7f", "2g2f", "7g7f 3c3d"]
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    config = InitialPositionConfig(
        type="file",
        source=str(source),
        source_format="usi_line",
        selection_policy="without_replacement",
        preserve_line_metadata=True,
    )

    entries = config.generate_entries(3, "seed")

    assert {entry.source_line for entry in entries} == set(lines)


def test_initial_position_config_refuses_oversized_selection_without_replacement(tmp_path: Path) -> None:
    source = tmp_path / "lines.usi"
    source.write_text("7g7f\n", encoding="utf-8")
    config = InitialPositionConfig(
        type="file",
        source=str(source),
        source_format="usi_line",
        selection_policy="without_replacement",
    )

    with pytest.raises(ValueError, match="requires at least 2 entries, got 1"):
        config.generate_entries(2, "seed")


def test_parse_engine_config_file_extracts_engine_path(tmp_path: Path) -> None:
    engine_file = tmp_path / "engine.yaml"
    engine_file.write_text(
        """
        name: sample
        engine_path: "/bin/echo"
        """,
        encoding="utf-8",
    )

    parsed = parse_engine_config_file(engine_file)

    assert parsed["engine_path"] == "/bin/echo"


def test_parse_engine_config_file_rejects_non_mapping(tmp_path: Path) -> None:
    engine_file = tmp_path / "engine-invalid.yaml"
    engine_file.write_text(
        """
        - /bin/echo
        - /bin/true
        """,
        encoding="utf-8",
    )

    with pytest.raises(TypeError, match="Configuration YAML must be a mapping"):
        parse_engine_config_file(engine_file)


def test_engine_overlay_applies_alias_fields_and_respects_explicit_values(tmp_path: Path) -> None:
    from shogiarena._core.contexts.game_session.adapters.orchestration.config_builders import _apply_engine_overlay
    from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig

    engine = EngineConfig(engine_path=tmp_path / "engine")
    _apply_engine_overlay(
        engine,
        {
            "engine": {
                "mate_default_infinite": True,
                "mate_wait_for_bestmove": True,
                "isready_sync_strategy": "wait",
                "isready_lock_skip_if_exists": True,
            }
        },
    )

    assert engine.is_mate_default_infinite is True
    assert engine.should_mate_wait_for_bestmove is True
    assert engine.isready_sync_strategy == "wait"
    assert engine.should_skip_isready_lock_if_exists is True

    explicit = EngineConfig(
        engine_path=tmp_path / "engine-explicit",
        mate_default_infinite=False,
        isready_sync_strategy="stop",
    )
    _apply_engine_overlay(explicit, {"engine": {"mate_default_infinite": True, "isready_sync_strategy": "wait"}})

    assert explicit.is_mate_default_infinite is False
    assert explicit.isready_sync_strategy == "stop"


def test_dashboard_config_defaults_to_loopback_host() -> None:
    from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import DashboardConfig

    assert DashboardConfig().api_host == "127.0.0.1"
    assert DashboardConfig(api_host="localhost").api_host == "localhost"
    assert DashboardConfig(api_host="::1").api_host == "::1"
    with pytest.raises(ValueError, match="explicit loopback host"):
        DashboardConfig(api_host="0.0.0.0")


def test_tournament_run_state_boundary_accepts_cancelled_game_with_opening_history() -> None:
    from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
    from shogiarena._core.shared.kernel.boundary_parsers.runner_state_payloads.parsers import (
        parse_tournament_run_state_boundary,
    )

    spec = GameSpec.create(
        black="eng_a",
        white="eng_b",
        sfen="lnsgkgsnl/1r5b1/pppppp1pp/6p2/9/2P6/PP1PPPPPP/1B5R1/LNSGKGSNL b - 3",
        round_num=0,
        seed="seed",
    )
    spec.display_order = 0
    spec.pair_key = "eng_a|eng_b|slot-0"
    spec.pair_slot = 0
    spec.pair_index = 0
    spec.matchup_key = "eng_a|eng_b"
    spec.opening_base_sfen = "startpos"
    spec.opening_moves_usi = ("7g7f", "3c3d")
    spec.opening_line_id = "lines.usi:1"
    spec.opening_line_moves_usi = ("7g7f", "3c3d")
    spec.opening_source = "lines.usi"
    spec.opening_source_line_no = 1

    cancelled_entry: dict[str, object] = {
        "game_id": spec.game_id,
        "black": spec.black_engine,
        "white": spec.white_engine,
        "round": spec.round_num,
        "sfen": spec.initial_sfen,
    }
    for key, value in spec.to_schedule_metadata().items():
        if key not in {"schema_version", "round_num", "initial_sfen"}:
            cancelled_entry[key] = value

    parsed = parse_tournament_run_state_boundary(
        {
            "schedule_hash": "sched",
            "resume_hash": "res",
            "total_games": 1,
            "completed_games_count": 0,
            "cancelled_game_ids": [spec.game_id],
            "cancelled_games": [cancelled_entry],
            "original_total_games": 2,
            "game_display_order": {spec.game_id: 0},
            "is_finished": False,
        }
    )

    cancelled = parsed["cancelled_games"]
    assert len(cancelled) == 1
    assert cancelled[0]["opening_base_sfen"] == "startpos"
    assert cancelled[0]["opening_moves_usi"] == ["7g7f", "3c3d"]


def test_resolve_opening_position_text_and_spsa_restored_game_handle_usi_lines() -> None:
    from shogiarena._core.contexts.spsa.adapters.orchestrator_gameplay_mixin import _build_restored_game
    from shogiarena._core.shared.kernel.initial_position_entry import resolve_opening_position_text

    bare_line = resolve_opening_position_text("7g7f 3c3d")
    assert bare_line.opening_base_sfen == "startpos"
    assert bare_line.opening_moves_usi == ("7g7f", "3c3d")
    assert bare_line.post_opening_sfen.endswith(" b - 3")

    with pytest.raises(ValueError, match="does not allow move sequences"):
        resolve_opening_position_text("startpos moves 7g7f", source_format="sfen")
    with pytest.raises(ValueError):
        resolve_opening_position_text("7g7f 3c3d", source_format="sfen")

    for start_sfen in (
        "startpos",
        "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1",
        "7g7f 3c3d",
    ):
        record = _build_restored_game(
            game_id="g1",
            start_sfen=start_sfen,
            black_player="tuned",
            white_player="baseline",
            result_kind="BLACK_WIN",
        )
        assert record.init_position_sfen.count(" ") == 3
