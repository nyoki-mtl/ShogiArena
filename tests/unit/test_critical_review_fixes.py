from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.game_session.adapters.orchestration.config_builders import _apply_engine_overlay
from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import DashboardConfig, EngineConfig
from shogiarena._core.contexts.game_session.application.completion.session_service import (
    TournamentSessionCompletionService,
)
from shogiarena._core.contexts.game_session.ports.completion_runtime import (
    CompletionMetadataContext,
    CompletionOpenBenchContext,
    CompletionPersistenceContext,
    CompletionRatingContext,
    CompletionRuntimeContext,
    CompletionStateContext,
)
from shogiarena._core.contexts.instances.application.engine_process_spawner import (
    _safe_remote_engine_dir_assignment,
)
from shogiarena._core.contexts.match.domain.adjudication import AdjudicationResult, ResignTracker
from shogiarena._core.contexts.spsa.adapters.runtime.persistence import update_index_json
from shogiarena._core.interfaces.cli.config.repo_setup import _should_use_github_token
from shogiarena._core.interfaces.cli.run.tournament import run_generate_command
from shogiarena._core.platform.engine_provisioning.remote_paths import RemotePathResolver
from shogiarena._core.platform.engine_provisioning.remote_repo_manager import RemoteRepoSpec, RemoteRepoSynchronizer
from shogiarena._core.platform.engine_provisioning.remote_stream_runner import build_remote_runner_command
from shogiarena._core.platform.settings.loader import write_settings_file
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.settings_loading.settings_models import ArenaSettings


def test_run_generate_command_accepts_cli_payload_arguments() -> None:
    signature = inspect.signature(run_generate_command)

    assert "config_payload" in signature.parameters
    assert "config_base_dir" in signature.parameters


def test_engine_overlay_applies_alias_fields_and_respects_explicit_values(tmp_path: Path) -> None:
    engine = EngineConfig(engine_path=tmp_path / "engine")

    _apply_engine_overlay(
        engine,
        {
            "engine": {
                "mate_default_infinite": True,
                "mate_wait_for_bestmove": True,
                "isready_sync_strategy": "wait",
                "isready_lock_skip_if_exists": True,
            }
        },
    )

    assert engine.is_mate_default_infinite is True
    assert engine.should_mate_wait_for_bestmove is True
    assert engine.isready_sync_strategy == "wait"
    assert engine.should_skip_isready_lock_if_exists is True

    explicit = EngineConfig(
        engine_path=tmp_path / "engine-explicit",
        mate_default_infinite=False,
        isready_sync_strategy="stop",
    )
    _apply_engine_overlay(explicit, {"engine": {"mate_default_infinite": True, "isready_sync_strategy": "wait"}})

    assert explicit.is_mate_default_infinite is False
    assert explicit.isready_sync_strategy == "stop"


@pytest.mark.asyncio
async def test_paused_completion_does_not_mark_game_complete() -> None:
    saved: list[None] = []
    synced: list[None] = []

    async def sync_after_game() -> None:
        synced.append(None)

    service = TournamentSessionCompletionService()
    context = CompletionRuntimeContext(
        metadata=CompletionMetadataContext(
            is_generate_run=False,
            summary_source="tournament",
            experiment_name=None,
            record_format=None,
        ),
        persistence=CompletionPersistenceContext(db_service=None, record_writer=None),
        rating=CompletionRatingContext(rating_service=None),
        state=CompletionStateContext(
            completed_game_ids=set(),
            completed_game_summaries={},
            sprt_service=None,
            sprt_pair=None,
            sprt_min_games=0,
            stop_controller=SimpleNamespace(request_stop=lambda **_kwargs: None),
            is_dashboard_enabled=True,
            total_games=1,
            save_run_state=lambda: saved.append(None),
        ),
        openbench=CompletionOpenBenchContext(is_strict_mode=False, sync_after_game=sync_after_game),
    )

    update_dashboard, result = await service.process_game_completion(
        context,
        SimpleNamespace(
            game_id="g0001",
            black_engine="black",
            white_engine="white",
            round_num=0,
            initial_sfen="startpos",
        ),
        record=SimpleNamespace(result=GameResult.PAUSED),
        is_stop_requested=True,
        extract_participation=lambda _record: (),
        openbench_error_type=RuntimeError,
    )

    assert update_dashboard is False
    assert result == GameResult.PAUSED
    assert context.state.completed_game_ids == set()
    assert context.state.completed_game_summaries == {}
    assert saved == []
    assert synced == []


def test_two_sided_resign_counts_agreement_for_same_winner() -> None:
    tracker = ResignTracker(resign_score_cp=800, move_count=1, is_two_sided=True)

    assert tracker.update(-900, "cp", is_side_to_move_black=True) is None
    assert tracker.update(-900, "cp", is_side_to_move_black=False) == AdjudicationResult.RESIGN_BLACK


def test_spsa_update_index_uses_one_based_update_count(tmp_path: Path) -> None:
    now = "2026-07-03T00:00:00+00:00"
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "type": "spsa",
                "created_at": now,
                "updated_at": now,
                "is_finished": False,
                "completed_updates": 0,
                "total_updates": 3,
            }
        ),
        encoding="utf-8",
    )
    config = SimpleNamespace(num_updates=3)

    update_index_json(
        tmp_path,
        config,
        update_idx=1,
        params={"p": 1.0},
        s_plus=1.0,
        s_minus=0.0,
        step=0.5,
        gradients={"p": 1.0},
        deltas={"p": 1.0},
        delta_norm=0.5,
        batch_size=2,
        total_games=2,
        timestamp=1,
        a_k=1.0,
        c_k=1.0,
        iteration_k=1,
        perturbations=None,
    )

    state_after_first = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state_after_first["completed_updates"] == 1
    assert state_after_first["is_finished"] is False

    update_index_json(
        tmp_path,
        config,
        update_idx=3,
        params={"p": 3.0},
        s_plus=1.0,
        s_minus=0.0,
        step=0.5,
        gradients={"p": 1.0},
        deltas={"p": 1.0},
        delta_norm=0.5,
        batch_size=2,
        total_games=2,
        timestamp=2,
        a_k=1.0,
        c_k=1.0,
        iteration_k=3,
        perturbations=None,
    )

    state_after_third = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state_after_third["completed_updates"] == 3
    assert state_after_third["is_finished"] is True


class _FakeTransport:
    def __init__(self, *, env_output: str = "") -> None:
        self.env_output = env_output
        self.commands: list[str] = []

    async def run(self, command: str) -> tuple[int, str, str]:
        self.commands.append(command)
        return 0, self.env_output, ""


@pytest.mark.asyncio
async def test_remote_path_resolver_expands_without_eval() -> None:
    transport = _FakeTransport(env_output="__HOME__=/home/arena\nHOME=/home/arena\nENGINE_DIR=/opt/engines\n")
    resolver = RemotePathResolver(transport)

    assert await resolver.expand("~/book") == "/home/arena/book"
    assert await resolver.expand("$ENGINE_DIR/bin") == "/opt/engines/bin"
    assert all("eval" not in command for command in transport.commands)


@pytest.mark.asyncio
async def test_remote_repo_branch_checkout_uses_origin_ref() -> None:
    transport = _FakeTransport()
    synchronizer = RemoteRepoSynchronizer(
        transport,
        RemoteRepoSpec(base="/repo", url="https://github.com/a/b", ref="main"),
    )

    await synchronizer._reset_ref("/repo", "main")

    assert synchronizer._checkout_command("/repo", "main") == ("git -C /repo checkout -B main refs/remotes/origin/main")
    assert transport.commands == ["git -C /repo reset --hard refs/remotes/origin/main"]


def test_remote_runner_command_preserves_exit_code_sentinel() -> None:
    command = build_remote_runner_command("/repo", "/tmp/spec.json", "{}", github_token_file=None)

    assert "|| rc=$?" in command
    assert "echo __REMOTE_EXIT_RC:$rc" in command


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="リモート(Linux)ホスト上で実行される sh スニペットの検証。Windows にはローカル POSIX シェルの前提がない",
)
def test_safe_remote_engine_dir_assignment_has_no_eval_and_expands_home() -> None:
    script = _safe_remote_engine_dir_assignment("~/engines") + '; printf "%s" "$ENGINE_DIR"'

    output = subprocess.check_output(["bash", "-lc", script], text=True)

    assert "eval" not in script
    assert output.endswith("/engines")


def test_github_token_host_match_is_exact_or_subdomain() -> None:
    assert _should_use_github_token("https://github.com/example/repo.git") is True
    assert _should_use_github_token("https://uploads.github.com/example/repo.git") is True
    assert _should_use_github_token("https://evilgithub.com/example/repo.git") is False


def test_settings_file_never_stores_the_github_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """settings ファイルに token 本体が書かれないこと。

    token は環境変数から解決するため、settings ファイル自体は秘密を持たない。
    これが成り立つので 0o600 のようなパーミッション保護も不要になっている
    （Windows では chmod が read-only ビットしか反映せず強制もできない）。
    """
    settings_path = tmp_path / "settings.yaml"
    secret = "ghp_thismustnotbepersisted"
    monkeypatch.setenv("SHOGIARENA_GITHUB_TOKEN", secret)

    write_settings_file(
        settings_path,
        output_dir=tmp_path / "out",
        engine_dir=tmp_path / "engines",
        github_token_env="SHOGIARENA_GITHUB_TOKEN",
    )

    content = settings_path.read_text(encoding="utf-8")
    assert secret not in content
    assert "github_token:" not in content


def test_settings_resolve_github_token_from_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CUSTOM_GH_TOKEN", "ghp_fromenv")
    settings = ArenaSettings(
        output_dir=tmp_path / "out",
        engine_dir=tmp_path / "engines",
        settings_path=tmp_path / "settings.yaml",
        repos={},
        overlays={},
        openbench=None,
        github_token_env="CUSTOM_GH_TOKEN",
    )

    assert settings.github_token == "ghp_fromenv"

    monkeypatch.delenv("CUSTOM_GH_TOKEN")
    assert settings.github_token is None


def test_dashboard_config_defaults_to_loopback_host() -> None:
    assert DashboardConfig().api_host == "127.0.0.1"
    assert DashboardConfig(api_host="localhost").api_host == "localhost"
    assert DashboardConfig(api_host="::1").api_host == "::1"
    with pytest.raises(ValueError, match="explicit loopback host"):
        DashboardConfig(api_host="0.0.0.0")
