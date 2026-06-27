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


def test_from_mapping_defaults_engine_lifecycle_to_reuse(tmp_path: Path) -> None:
    cfg = TournamentRunConfig.from_mapping(_minimal_tournament_mapping(), base_dir=tmp_path)

    assert cfg.tournament.engine_lifecycle == "reuse"
    assert cfg.system.resource_capacity_preflight == "error"


def test_from_mapping_accepts_per_game_engine_lifecycle(tmp_path: Path) -> None:
    payload = _minimal_tournament_mapping()
    tournament = payload["tournament"]
    assert isinstance(tournament, dict)
    tournament["engine_lifecycle"] = "per_game"

    cfg = TournamentRunConfig.from_mapping(payload, base_dir=tmp_path)

    assert cfg.tournament.engine_lifecycle == "per_game"


def test_from_mapping_rejects_unknown_engine_lifecycle(tmp_path: Path) -> None:
    payload = _minimal_tournament_mapping()
    tournament = payload["tournament"]
    assert isinstance(tournament, dict)
    tournament["engine_lifecycle"] = "per_pair"

    with pytest.raises(ValueError, match="engine_lifecycle"):
        TournamentRunConfig.from_mapping(payload, base_dir=tmp_path)


def test_from_mapping_rejects_unknown_engine_keys(tmp_path: Path) -> None:
    payload = _minimal_tournament_mapping()
    engines = payload["engines"]
    assert isinstance(engines, list)
    first = engines[0]
    assert isinstance(first, dict)
    first["environment"] = {"RSHOGI_TRACE": "1"}

    with pytest.raises(ValueError, match="environment"):
        TournamentRunConfig.from_mapping(payload, base_dir=tmp_path)


def test_from_mapping_accepts_engine_go_options(tmp_path: Path) -> None:
    payload = _minimal_tournament_mapping()
    engines = payload["engines"]
    assert isinstance(engines, list)
    first = engines[0]
    assert isinstance(first, dict)
    first["go_options"] = {"nodes": 1000}

    cfg = TournamentRunConfig.from_mapping(payload, base_dir=tmp_path)

    assert cfg.engines[0].go_options == {"nodes": 1000}


def test_from_mapping_rejects_timing_engine_go_options(tmp_path: Path) -> None:
    payload = _minimal_tournament_mapping()
    engines = payload["engines"]
    assert isinstance(engines, list)
    first = engines[0]
    assert isinstance(first, dict)
    first["go_options"] = {"movetime": 1000}

    with pytest.raises(ValueError, match="go_options.movetime"):
        TournamentRunConfig.from_mapping(payload, base_dir=tmp_path)


def test_from_mapping_rejects_time_control_depth_alias(tmp_path: Path) -> None:
    payload = _minimal_tournament_mapping()
    payload["rules"] = {
        "time_control": {
            "depth": 9,
            "expiry_margin_ms": 500,
            "max_wait_ms": 600_000,
        }
    }

    with pytest.raises(ValueError, match="depth.*depth_limit"):
        TournamentRunConfig.from_mapping(payload, base_dir=tmp_path)


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


_BOOK_DB = (
    "#YANEURAOU-DB2016 1.00\nsfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1\n7g7f none 0 32 1\n"
)


def _book_engine_payload(tmp_path: Path) -> dict[str, object]:
    engine_binary = tmp_path / "engine-a"
    engine_binary.write_text("#!/bin/sh\n", encoding="utf-8")
    engine_config = tmp_path / "engine-a.yaml"
    engine_config.write_text(
        "name: engine-a\n"
        f'engine_path: "{engine_binary}"\n'
        "options:\n"
        "  BookDir: book\n"
        "  BookFile: user_book1.db\n"
        '  USI_OwnBook: "true"\n',
        encoding="utf-8",
    )
    return {
        "experiment_name": "tournament",
        "engines": [
            {"name": "dev", "engine_path": str(engine_config)},
            {"name": "base", "artifact": "YaneuraOu/eb2856f9"},
        ],
        "rules": {},
        "system": {"path_preflight": "error"},
    }


def test_path_preflight_relative_book_dir_resolved_against_engine_working_dir(tmp_path: Path) -> None:
    # BookDir: book は engine 実行 cwd（engine binary の親 = tmp_path）基準で解決され、
    # tmp_path/book/user_book1.db が存在すれば preflight は通る（scalar BookDir の cwd 誤検知を回避）。
    book_dir = tmp_path / "book"
    book_dir.mkdir()
    (book_dir / "user_book1.db").write_text(_BOOK_DB, encoding="utf-8")

    cfg = TournamentRunConfig.from_mapping(_book_engine_payload(tmp_path), base_dir=tmp_path)
    assert cfg.experiment_name == "tournament"


def test_path_preflight_relative_book_missing_under_working_dir_is_error(tmp_path: Path) -> None:
    # book ディレクトリを作らない -> composite 検証が実体不在で落ちる。
    with pytest.raises(FileNotFoundError, match="opening book file does not exist"):
        TournamentRunConfig.from_mapping(_book_engine_payload(tmp_path), base_dir=tmp_path)


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


def test_from_mapping_accepts_resource_capacity_preflight_mode(tmp_path: Path) -> None:
    payload = _minimal_tournament_mapping()
    payload["system"] = {"resource_capacity_preflight": "warn"}

    cfg = TournamentRunConfig.from_mapping(payload, base_dir=tmp_path)

    assert cfg.system.resource_capacity_preflight == "warn"
