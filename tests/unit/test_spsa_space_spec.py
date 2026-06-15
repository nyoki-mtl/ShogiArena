from __future__ import annotations

import pytest

from shogiarena._core.contexts.spsa.application.space_spec import (
    parse_spsa_space_spec,
    parse_spsa_tunable_manifest,
)


def _manifest() -> dict[str, object]:
    return {
        "schema_version": "shogiarena.usi_tunables.v1",
        "tunables": [
            {
                "id": "cpuct",
                "option": "Tune.Cpuct",
                "value_type": "float",
                "encoding": "decimal",
                "default": 1.5,
                "min": 0.5,
                "max": 4.0,
                "schedule": {"c_end": 0.1, "r_end": 0.002},
            },
            {
                "id": "visits",
                "option": "Tune.Visits",
                "value_type": "int",
                "encoding": "integer",
                "default": 8,
                "min": 1,
                "max": 64,
                "schedule": {"c_end": 2, "r_end": 0.003},
            },
        ],
    }


def test_space_select_resolves_manifest_with_overrides() -> None:
    space = parse_spsa_space_spec(
        {
            "schema_version": "shogiarena.spsa.space.v1",
            "target": {"protocol": "usi_options"},
            "select": [{"id": "cpuct"}, {"id": "visits"}],
            "overrides": {
                "cpuct": {
                    "initial": 1.745,
                    "schedule": {"c_end": 0.05},
                }
            },
        },
        manifest=parse_spsa_tunable_manifest(_manifest()),
    )

    cpuct, visits = space.parameters
    assert cpuct.id == "cpuct"
    assert cpuct.option == "Tune.Cpuct"
    assert cpuct.initial == 1.745
    assert cpuct.minimum == 0.5
    assert cpuct.c_end == 0.05
    assert cpuct.r_end == 0.002
    assert visits.id == "visits"
    assert visits.value_encoding == "integer"


def test_space_rejects_int_param_with_no_integer_in_bounds() -> None:
    with pytest.raises(ValueError, match="no integer"):
        parse_spsa_space_spec(
            {
                "schema_version": "shogiarena.spsa.space.v1",
                "target": {"protocol": "usi_options"},
                "parameters": [
                    {
                        "id": "p",
                        "target": {"option": "Tune.P", "value_encoding": "integer"},
                        "value_type": "int",
                        "initial": 2.5,
                        "bounds": {"min": 2.2, "max": 2.8},
                        "schedule": {"c_end": 1, "r_end": 0.002},
                    }
                ],
            }
        )


def test_space_rejects_manifest_int_param_with_no_integer_in_bounds() -> None:
    manifest = {
        "schema_version": "shogiarena.usi_tunables.v1",
        "tunables": [
            {
                "id": "p",
                "option": "Tune.P",
                "value_type": "int",
                "encoding": "integer",
                "default": 2.5,
                "min": 2.2,
                "max": 2.8,
                "schedule": {"c_end": 1, "r_end": 0.002},
            }
        ],
    }
    with pytest.raises(ValueError, match="no integer"):
        parse_spsa_space_spec(
            {
                "schema_version": "shogiarena.spsa.space.v1",
                "target": {"protocol": "usi_options"},
                "select": [{"id": "p"}],
            },
            manifest=manifest,
        )


def test_space_select_requires_manifest() -> None:
    with pytest.raises(ValueError, match="requires a tunable manifest"):
        parse_spsa_space_spec(
            {
                "schema_version": "shogiarena.spsa.space.v1",
                "target": {"protocol": "usi_options"},
                "select": [{"id": "cpuct"}],
            }
        )


def test_space_select_rejects_unknown_manifest_id() -> None:
    with pytest.raises(ValueError, match="selected tunable is not in manifest"):
        parse_spsa_space_spec(
            {
                "schema_version": "shogiarena.spsa.space.v1",
                "target": {"protocol": "usi_options"},
                "select": [{"id": "unknown"}],
            },
            manifest=_manifest(),
        )
