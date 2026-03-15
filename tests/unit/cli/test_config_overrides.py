from __future__ import annotations

import pytest

from shogiarena._core.interfaces.cli.main import CliArgumentError
from shogiarena._core.interfaces.cli.run.config_overrides import apply_section_overrides, parse_scalar
from shogiarena._core.shared.kernel.json_types import JsonObject


def test_parse_scalar_parses_yaml_value() -> None:
    assert parse_scalar("true", label="override value") is True
    assert parse_scalar("3.14", label="override value") == 3.14


def test_parse_scalar_raises_cli_argument_error_for_invalid_yaml() -> None:
    with pytest.raises(CliArgumentError, match="failed to parse"):
        parse_scalar("[1, 2", label="override value")


def test_apply_section_overrides_applies_yaml_value() -> None:
    payload: JsonObject = {}

    apply_section_overrides(payload, "rules", ["max_games=128"])

    assert payload == {"rules": {"max_games": 128}}
