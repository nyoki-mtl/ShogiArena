from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from shogiarena._core.interfaces.cli.main import main
from shogiarena._core.platform.db.store.entities import Base, Game, Player


def _create_result_db(db_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        engine_a = Player(game_type="shogi", player_name="engine-a")
        engine_b = Player(game_type="shogi", player_name="engine-b")
        session.add_all([engine_a, engine_b])
        session.flush()
        session.add_all(
            [
                Game(
                    game_type="shogi",
                    game_name="g1",
                    game_result="BLACK_WIN",
                    num_moves=1,
                    black_player_id=engine_a.id,
                    white_player_id=engine_b.id,
                    initial_position_sfen="startpos",
                    updated_date=datetime(2026, 6, 5, 0, 0, 0),
                ),
                Game(
                    game_type="shogi",
                    game_name="g2",
                    game_result="DRAW_BY_REPETITION",
                    num_moves=1,
                    black_player_id=engine_b.id,
                    white_player_id=engine_a.id,
                    initial_position_sfen="startpos",
                    updated_date=datetime(2026, 6, 5, 0, 0, 1),
                ),
            ]
        )
        session.commit()


def test_results_summary_command_outputs_json_for_run_dir(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _create_result_db(run_dir / "game.db")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "provenance_sealed",
                "shogiarena_version": "0.test",
                "hashes": {"resume_hash": "resume"},
                "tournament": {"total_scheduled_games": 4},
            }
        ),
        encoding="utf-8",
    )
    failure_dir = run_dir / "failures"
    failure_dir.mkdir()
    (failure_dir / "run_failures.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "failures": [
                    {
                        "game_id": "g3",
                        "scheduled_black_engine": "engine-a",
                        "scheduled_white_engine": "engine-b",
                        "failure_phase": "engine_start",
                        "exception_class": "UsiEngineStartError",
                        "short_message": "failed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    main(["results", "summary", str(run_dir), "--format", "json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema_version"] == 1
    assert payload["run_dir"] == str(run_dir)
    assert payload["shogiarena_version"] == "0.test"
    assert payload["manifest_status"] == "provenance_sealed"
    assert payload["is_resumable"] is True
    assert payload["completed_games"] == 2
    assert payload["total_scheduled_games"] == 4
    assert payload["incomplete_games"] == 2
    assert payload["failed_games"] == 1
    assert payload["raw_result_counts"] == {
        "BLACK_WIN": 1,
        "DRAW_BY_REPETITION": 1,
    }
    assert payload["failures_by_phase"] == {"engine_start": 1}
    assert payload["failures"][0]["game_id"] == "g3"
    engines = {entry["engine"]: entry for entry in payload["engines"]}
    assert engines["engine-a"]["wins"] == 1
    assert engines["engine-a"]["draws"] == 1


def test_results_summary_command_can_filter_engine(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "game.db"
    _create_result_db(db_path)

    main(["results", "summary", str(db_path), "--format", "csv", "--engine", "engine-a"])

    captured = capsys.readouterr()
    assert "engine-a" in captured.out
    assert "engine-b" not in captured.out


def test_results_summary_command_rejects_invalid_confidence(tmp_path: Path) -> None:
    db_path = tmp_path / "game.db"
    _create_result_db(db_path)

    with pytest.raises(SystemExit) as exc_info:
        main(["results", "summary", str(db_path), "--confidence", "1.5"])

    assert exc_info.value.code == 1


def test_results_summary_command_handles_corrupted_game_result(tmp_path: Path) -> None:
    db_path = tmp_path / "game.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        engine_a = Player(game_type="shogi", player_name="engine-a")
        engine_b = Player(game_type="shogi", player_name="engine-b")
        session.add_all([engine_a, engine_b])
        session.flush()
        session.add(
            Game(
                game_type="shogi",
                game_name="g1",
                game_result="NOT_A_REAL_RESULT",
                num_moves=1,
                black_player_id=engine_a.id,
                white_player_id=engine_b.id,
                initial_position_sfen="startpos",
                updated_date=datetime(2026, 6, 5, 0, 0, 0),
            )
        )
        session.commit()

    # A corrupted game_result must surface as a clean CLI error (exit 1), not a
    # raw ValueError traceback.
    with pytest.raises(SystemExit) as exc_info:
        main(["results", "summary", str(db_path), "--format", "json"])

    assert exc_info.value.code == 1


def test_results_verify_provenance_reports_ok(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    engine_path = tmp_path / "engine.bin"
    engine_path.write_bytes(b"engine")
    digest = "ed9f6f25068608efd412958da4dfc19328ca3511251fa6d5f9c42baf230e32f8"
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "provenance_sealed",
                "engines": [
                    {
                        "name": "engine-a",
                        "resolved_paths": {"engine_path": str(engine_path), "path_options": {}},
                        "bytes_hash": {"engine_binary_sha256": digest, "path_options": {}},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    main(["results", "verify-provenance", str(run_dir)])

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["failures"] == []


def test_results_verify_provenance_exits_for_mismatch(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    engine_path = tmp_path / "engine.bin"
    engine_path.write_bytes(b"engine")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "provenance_sealed",
                "engines": [
                    {
                        "name": "engine-a",
                        "resolved_paths": {"engine_path": str(engine_path), "path_options": {}},
                        "bytes_hash": {"engine_binary_sha256": "0" * 64, "path_options": {}},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    try:
        main(["results", "verify-provenance", str(run_dir)])
    except SystemExit as exc:
        assert exc.code == 1
    else:  # pragma: no cover - defensive
        raise AssertionError("verify-provenance should fail for a hash mismatch")

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["failures"][0]["reason"] == "engine_binary_sha256_mismatch"


def test_results_verify_provenance_rejects_inputs_only_manifest(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"schema_version": 2, "status": "inputs_only", "hashes": {"resume_hash": None}}),
        encoding="utf-8",
    )

    try:
        main(["results", "verify-provenance", str(run_dir)])
    except SystemExit as exc:
        assert exc.code == 1
    else:  # pragma: no cover - defensive
        raise AssertionError("verify-provenance should fail for inputs_only manifest")

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["failures"][0]["reason"] == "manifest_not_sealed"
