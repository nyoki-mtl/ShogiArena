"""Release evidence for finalizer failures through production composition."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_tournament_composition_timeout import (
    _write_config,
    _write_engine_assets,
)

from shogiarena._core.contexts.game_session.application.summary.artifact_service import (
    TournamentSummaryArtifactService,
)
from shogiarena._core.platform.db.store.arena_db_adapter import ArenaDBAdapter
from shogiarena.tournament import build_tournament_runner, create_run_storage, load_tournament_config


@pytest.mark.asyncio
async def test_real_run_finalizer_failure_cannot_publish_a_clean_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """実 subprocess の対局後に final write を失敗させ、failure commit と cleanup を確認する。"""

    black, white = _write_engine_assets(tmp_path, delay_s=0.0)
    config_path = _write_config(
        tmp_path,
        black=black,
        white=white,
        time_ms=5_000,
        margin_ms=500,
    )
    run_dir = tmp_path / "run"
    runner = build_tournament_runner(
        load_tournament_config(config_path),
        storage=create_run_storage(run_dir),
        should_skip_resume=True,
    )

    def fail_final_results_write(_run_dir: Path, _payload: dict[str, object]) -> None:
        raise RuntimeError("injected final results write failure")

    monkeypatch.setattr(
        TournamentSummaryArtifactService,
        "write_tournament_results",
        staticmethod(fail_final_results_write),
    )

    with pytest.raises(RuntimeError, match="injected final results write failure"):
        await runner.run()

    status = json.loads((run_dir / "completion_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["termination_reason"] == "finalization-error"
    assert status["is_provisional"] is False
    assert not (run_dir / "completed.flag").exists()

    assert runner.are_services_closed()
    assert runner._state.db_service is None  # type: ignore[attr-defined]

    # Windows では接続 pool が残ると rename が WinError 32 で失敗する。
    # lifecycle flag だけでなく、実際の game.db handle が解放されたことを固定する。
    db_path = run_dir / "game.db"
    renamed_path = run_dir / "game.closed.db"
    db_path.rename(renamed_path)
    renamed_path.rename(db_path)


@pytest.mark.asyncio
async def test_real_run_db_cleanup_failure_cannot_publish_a_clean_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """production DB close failure を finalizer が clean completion と誤認しないこと。"""

    black, white = _write_engine_assets(tmp_path, delay_s=0.0)
    config_path = _write_config(
        tmp_path,
        black=black,
        white=white,
        time_ms=5_000,
        margin_ms=500,
    )
    run_dir = tmp_path / "run"
    runner = build_tournament_runner(
        load_tournament_config(config_path),
        storage=create_run_storage(run_dir),
        should_skip_resume=True,
    )
    original_close = ArenaDBAdapter.close

    def fail_after_db_close(adapter: ArenaDBAdapter) -> None:
        original_close(adapter)
        raise RuntimeError("injected DB cleanup failure")

    monkeypatch.setattr(ArenaDBAdapter, "close", fail_after_db_close)

    with pytest.raises(RuntimeError, match="injected DB cleanup failure"):
        await runner.run()

    status = json.loads((run_dir / "completion_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["termination_reason"] == "cleanup-error"
    assert status["is_provisional"] is False
    assert not (run_dir / "completed.flag").exists()
    assert runner._state.db_service is None  # type: ignore[attr-defined]
