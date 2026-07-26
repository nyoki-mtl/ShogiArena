from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.config_tournament import TournamentRunConfig
from shogiarena._core.contexts.game_session.adapters.run_storage import FilesystemRunStorage
from shogiarena._core.contexts.game_session.application.session.run_service import TournamentSessionRunService
from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService
from shogiarena._core.contexts.tournament.adapters.runner import TournamentRunner


def _build_runner(tmp_path: Path) -> TournamentRunner:
    engines = []
    for name in ("dev", "base"):
        path = tmp_path / f"{name}.yaml"
        path.write_text("name: dummy\n", encoding="utf-8")
        engines.append({"name": name, "engine_path": path})
    config = TournamentRunConfig(experiment_name="watchdog-lifecycle-test", engines=engines)
    return TournamentRunner(
        config,
        storage=FilesystemRunStorage(tmp_path / "run"),
        should_skip_resume=True,
        engine_factory_service=EngineFactoryService(factory=AsyncMock()),
        init_dashboard_html=MagicMock(),
        api_server_factory=MagicMock(),
    )


@pytest.mark.asyncio
async def test_tournament_run_starts_runtime_watchdog(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`TournamentRunner.run` は base の run を上書きするので、watchdog 起動が漏れやすい。

    漏れると timeout attribution が黙って UNATTRIBUTED に倒れ、completion_status.json の
    watchdog も null になる（task 0047 の再分類が本番経路で無効化される）。
    """

    runner = _build_runner(tmp_path)
    observed: dict[str, object] = {}

    async def fake_run(_service: object, runner_arg: TournamentRunner, **_kwargs: object) -> None:
        watchdog = runner_arg._watchdog
        observed["watchdog"] = watchdog
        observed["summary"] = watchdog.summary() if watchdog is not None else None
        return None

    monkeypatch.setattr(TournamentSessionRunService, "run", fake_run)

    assert await runner.run() is None
    watchdog = observed["watchdog"]
    assert watchdog is not None
    assert observed["summary"] is not None
    # finalize は run の内側で走るが、停止後も集計を読めることまで固定しておく。
    assert runner._watchdog is watchdog
    assert watchdog.summary() is not None


def test_completion_context_shares_run_scoped_breaker_counters(tmp_path) -> None:
    """breaker counter は runner state の mapping をそのまま共有すること（task 0052）。

    completion context は局ごとに組み直されるため、context 側で新しい dict を作ると
    連続回数も比率も production run では閾値へ到達しない。
    """

    runner = _build_runner(tmp_path)
    first = runner._build_completion_runtime_context()  # noqa: SLF001
    second = runner._build_completion_runtime_context()  # noqa: SLF001

    assert first.state.consecutive_invalid_timeouts_by_origin is runner._state.consecutive_invalid_timeouts_by_origin  # noqa: SLF001
    assert first.state.invalid_timeouts_by_origin is runner._state.invalid_timeouts_by_origin  # noqa: SLF001
    # 別の局で組み直しても同じ mapping を指す。
    assert second.state.consecutive_invalid_timeouts_by_origin is first.state.consecutive_invalid_timeouts_by_origin
    assert second.state.invalid_timeouts_by_origin is first.state.invalid_timeouts_by_origin

    first.state.consecutive_invalid_timeouts_by_origin["unknown"] = 2
    assert second.state.consecutive_invalid_timeouts_by_origin["unknown"] == 2
