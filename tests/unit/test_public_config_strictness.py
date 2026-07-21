from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path
from typing import cast

import pytest
import yaml
from pydantic import ValidationError

from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import EngineConfig
from shogiarena._core.contexts.game_session.adapters.orchestration.engine_config_artifacts import (
    resolve_engine_config_entry,
)
from shogiarena.engine import UsiEngineConfig
from shogiarena.tournament import load_tournament_config


def _valid_tournament_payload() -> dict[str, object]:
    return {
        "experiment_name": "strict-config-test",
        "engines": [
            {"name": "dev", "artifact": "YaneuraOu/a5ee2786"},
            {"name": "base", "artifact": "YaneuraOu/eb2856f9"},
        ],
        "tournament": {"games_per_pair": 4},
        "rules": {
            "time_control": {"node_limit": 100},
            "initial_positions": {},
            "adjudication": {},
        },
        "sprt": {"elo0": 0.0, "elo1": 5.0},
        "openbench": {
            "enabled": False,
            "create": {"payload": {}},
        },
        "rating": {},
        "dashboard": {"enabled": False},
        "logging": {},
        "records_output": {"format": "sbinpack"},
    }


@pytest.mark.parametrize(
    "section_path",
    [
        ("tournament",),
        ("rules",),
        ("rules", "time_control"),
        ("rules", "initial_positions"),
        ("rules", "adjudication"),
        ("sprt",),
        ("openbench",),
        ("openbench", "create"),
        ("openbench", "create", "payload"),
        ("rating",),
        ("dashboard",),
        ("logging",),
        ("records_output",),
    ],
)
def test_public_load_tournament_config_rejects_nested_unknown_keys(
    section_path: tuple[str, ...],
    tmp_path: Path,
) -> None:
    payload = _valid_tournament_payload()
    section: MutableMapping[str, object] = payload
    for part in section_path:
        nested = section[part]
        assert isinstance(nested, MutableMapping)
        section = cast(MutableMapping[str, object], nested)
    section["unexpected_typo"] = True

    with pytest.raises(ValidationError, match="unexpected_typo"):
        load_tournament_config(payload, base_dir=tmp_path)


def test_public_load_tournament_config_preserves_system_unknown_keys_as_extras(tmp_path: Path) -> None:
    payload = _valid_tournament_payload()
    payload["system"] = {"vendor_resource_hint": 3}

    config = load_tournament_config(payload, base_dir=tmp_path)

    assert config.system.extras == {"vendor_resource_hint": 3}


def test_public_load_tournament_config_rejects_unknown_engine_keys(tmp_path: Path) -> None:
    payload = _valid_tournament_payload()
    engines = payload["engines"]
    assert isinstance(engines, list)
    engine = engines[0]
    assert isinstance(engine, dict)
    engine = cast(dict[str, object], engine)
    engine["unexpected_typo"] = True

    with pytest.raises(ValidationError, match="unexpected_typo"):
        load_tournament_config(payload, base_dir=tmp_path)


def test_public_load_tournament_config_rejects_unknown_generate_keys(tmp_path: Path) -> None:
    payload = _valid_tournament_payload()
    engines = payload["engines"]
    assert isinstance(engines, list)
    payload["engines"] = engines[:1]
    payload.pop("tournament")
    payload.pop("sprt")
    payload["generate"] = {"games": 1, "unexpected_typo": True}

    with pytest.raises(ValidationError, match="unexpected_typo"):
        load_tournament_config(payload, base_dir=tmp_path)


def test_engine_config_rejects_unknown_keys() -> None:
    # typo を黙って捨てると、指定したはずの option が無効のまま対局が走る。
    with pytest.raises(TypeError, match="optiosn"):
        UsiEngineConfig.from_mapping(
            {
                "name": "engine",
                "engine_path": "engine.exe",
                "optiosn": {"Threads": 4},
            }
        )


def test_engine_config_accepts_sealed_artifact_provenance_keys(tmp_path: Path) -> None:
    """自分たちが生成した sealed artifact config を strict 化で拒否しないこと。"""

    (tmp_path / "overlay.yaml").write_text("options:\n  Threads: 4\n", encoding="utf-8")
    engine = EngineConfig(
        name="dev",
        artifact="YaneuraOu/a5ee2786",
        build_options={"target": "avx2"},
        options={"Threads": 4},
        options_overlays=[tmp_path / "overlay.yaml"],
        path_options=["EvalDir"],
    )

    resolved = resolve_engine_config_entry(
        engine,
        output_dir=tmp_path / "engine_configs",
        extra_options=None,
        artifact_resolver=lambda artifact, build_options: str(tmp_path / "engine.exe"),
    )

    assert resolved.engine_path is not None
    sealed_payload = yaml.safe_load(resolved.engine_path.read_text(encoding="utf-8"))
    assert "options_overlays" in sealed_payload
    assert "path_options" in sealed_payload

    loaded = UsiEngineConfig.from_mapping(sealed_payload)

    assert loaded.name == "dev"
    assert loaded.options["Threads"] == 4
