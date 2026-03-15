"""Config boundary parser tests for invalid/valid payloads."""

from __future__ import annotations

from pathlib import Path

import pytest

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
    params_file = tmp_path / "params.txt"
    start_file.write_text("startpos\n", encoding="utf-8")
    params_file.write_text("p1, float, 1.0, 0.0, 10.0\n", encoding="utf-8")
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
            "parameters_path": str(params_file),
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
