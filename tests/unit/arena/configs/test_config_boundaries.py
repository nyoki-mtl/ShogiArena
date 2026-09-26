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
