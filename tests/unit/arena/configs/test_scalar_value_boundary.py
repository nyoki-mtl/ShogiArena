from __future__ import annotations

import pytest

from shogiarena._core.interfaces.boundaries.parsers.json_object import parse_yaml_value_boundary


def test_parse_yaml_value_boundary_parses_scalar_and_mapping() -> None:
    assert parse_yaml_value_boundary("42", label="value") == 42
    assert parse_yaml_value_boundary("{threads: 2}", label="value") == {"threads": 2}


def test_parse_yaml_value_boundary_rejects_empty_value() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        parse_yaml_value_boundary("", label="override value")


def test_parse_yaml_value_boundary_rejects_invalid_yaml() -> None:
    with pytest.raises(ValueError, match="failed to parse"):
        parse_yaml_value_boundary("[1, 2", label="override value")
