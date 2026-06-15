from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from shogiarena._core.contexts.game_session.adapters.openbench import (
    create_payload_builder as _openbench_create_payload_builder_mod,
)
from shogiarena._core.contexts.game_session.adapters.openbench.client import OpenBenchClient
from shogiarena._core.contexts.game_session.adapters.openbench.client_totals import (
    compute_totals as _compute_totals,
)
from shogiarena._core.contexts.game_session.adapters.openbench.client_types import (
    OpenBenchClientConfig,
    OpenBenchCounters,
    OpenBenchError,
)
from shogiarena._core.contexts.game_session.adapters.openbench.client_worker_capabilities import (
    _derive_worker_capabilities,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.config_core import (
    OpenBenchConfig,
    SprtConfig,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.config_tournament import TournamentRunConfig
from shogiarena._core.contexts.game_session.adapters.run_storage import FilesystemRunStorage
from shogiarena._core.contexts.tournament.adapters.runner import TournamentRunner
from shogiarena._core.platform.settings import facade as settings_mod
from shogiarena._core.shared.kernel.game_results import GameResult
from shogiarena._core.shared.kernel.settings_loading.settings_models import OpenBenchSettings


def _engine_config_file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text("name: dummy\n", encoding="utf-8")
    return path


def test_openbench_config_requires_target_test_when_enabled() -> None:
    with pytest.raises(ValueError, match="target_test_id"):
        OpenBenchConfig(
            enabled=True,
            server="https://example.com",
            username="user",
            password_env="OPENBENCH_PASSWORD",
        )


def test_openbench_config_create_mode_requires_create_payload() -> None:
    with pytest.raises(ValueError, match="openbench.create"):
        OpenBenchConfig(
            enabled=True,
            mode="create_test",
            server="https://example.com",
            username="user",
            password_env="OPENBENCH_PASSWORD",
        )


def test_openbench_config_create_mode_allows_missing_target_test_id() -> None:
    cfg = OpenBenchConfig(
        enabled=True,
        mode="create_test",
        server="https://example.com",
        username="user",
        password_env="OPENBENCH_PASSWORD",
        create={
            "payload": {
                "dev_engine": "YaneuraOu",
                "base_engine": "YaneuraOu",
                "dev_repo": "https://github.com/example/repo",
                "base_repo": "https://github.com/example/repo",
                "dev_branch": "3570416",
                "base_branch": "9f89431a",
                "dev_time_control": "N=100000",
                "base_time_control": "N=100000",
            }
        },
    )
    assert cfg.target_test_id is None
    assert cfg.mode == "create_test"
    assert cfg.create is not None


def test_tournament_config_rejects_openbench_without_sprt(tmp_path: Path) -> None:
    e1 = _engine_config_file(tmp_path, "e1")
    e2 = _engine_config_file(tmp_path, "e2")
    with pytest.raises(ValueError, match="requires sprt"):
        TournamentRunConfig(
            experiment_name="openbench-no-sprt",
            engines=[
                {"name": "dev", "engine_path": e1},
                {"name": "base", "engine_path": e2},
            ],
            openbench={
                "enabled": True,
                "server": "https://example.com",
                "username": "user",
                "password_env": "OPENBENCH_PASSWORD",
                "target_test_id": 1,
            },
        )


def test_compute_totals_for_openbench_payload() -> None:
    class _DummyDB:
        def get_games_with_players(self, *, game_type: str) -> list[dict[str, object]]:
            assert game_type == "arena"
            return [
                {
                    "game_name": "g0001-a",
                    "black_player": "dev",
                    "white_player": "base",
                    "result": GameResult.BLACK_WIN,
                    "initial_sfen": "sfen_a",
                },
                {
                    "game_name": "g0002-b",
                    "black_player": "base",
                    "white_player": "dev",
                    "result": GameResult.DRAW_BY_REPETITION,
                    "initial_sfen": "sfen_a",
                },
                {
                    "game_name": "g0003-c",
                    "black_player": "dev",
                    "white_player": "base",
                    "result": GameResult.WHITE_WIN_BY_TIMEOUT,
                    "initial_sfen": "sfen_b",
                },
                {
                    "game_name": "g0004-d",
                    "black_player": "base",
                    "white_player": "dev",
                    "result": GameResult.WHITE_WIN_BY_ILLEGAL_MOVE,
                    "initial_sfen": "sfen_b",
                },
            ]

    totals = _compute_totals(_DummyDB(), tested_engine="dev", base_engine="base")
    assert totals.wins == 2
    assert totals.losses == 1
    assert totals.draws == 1
    assert totals.timelosses == 1
    assert totals.illegals == 1
    assert totals.crashes == 0
    assert totals.dw == 1
    assert totals.dd == 1
    assert totals.ll == 0
    assert totals.ld == 0
    assert totals.ww == 0


def test_compute_totals_excludes_non_decided_games_crash_only() -> None:
    # Regression: ERROR/INVALID/PAUSED must not be folded into draws (W/D/L) or into the
    # pentanomial bins. ERROR/INVALID still count as crashes; PAUSED counts as nothing.
    class _DummyDB:
        def get_games_with_players(self, *, game_type: str) -> list[dict[str, object]]:
            return [
                # A complete decided pair -> WW.
                {
                    "game_name": "g0001-a",
                    "black_player": "dev",
                    "white_player": "base",
                    "result": GameResult.BLACK_WIN,
                    "initial_sfen": "sfen_a",
                },
                {
                    "game_name": "g0002-b",
                    "black_player": "base",
                    "white_player": "dev",
                    "result": GameResult.WHITE_WIN,
                    "initial_sfen": "sfen_a",
                },
                # A non-decided pair -> excluded entirely (previously scored as DD).
                {
                    "game_name": "g0003-c",
                    "black_player": "dev",
                    "white_player": "base",
                    "result": GameResult.ERROR,
                    "initial_sfen": "sfen_b",
                },
                {
                    "game_name": "g0004-d",
                    "black_player": "base",
                    "white_player": "dev",
                    "result": GameResult.PAUSED,
                    "initial_sfen": "sfen_b",
                },
            ]

    totals = _compute_totals(_DummyDB(), tested_engine="dev", base_engine="base")
    assert (totals.wins, totals.draws, totals.losses) == (2, 0, 0)
    assert totals.crashes == 1  # ERROR; PAUSED is not a crash
    assert [totals.ll, totals.ld, totals.dd, totals.dw, totals.ww] == [0, 0, 0, 0, 1]


def test_tournament_config_accepts_openbench_with_sprt(tmp_path: Path) -> None:
    e1 = _engine_config_file(tmp_path, "e1")
    e2 = _engine_config_file(tmp_path, "e2")
    cfg = TournamentRunConfig(
        experiment_name="openbench-with-sprt",
        engines=[
            {"name": "dev", "engine_path": e1},
            {"name": "base", "engine_path": e2},
        ],
        sprt=SprtConfig(),
        openbench={
            "enabled": True,
            "server": "https://example.com",
            "username": "user",
            "password_env": "OPENBENCH_PASSWORD",
            "target_test_id": 1,
        },
    )
    assert cfg.openbench is not None
    assert cfg.openbench.is_enabled is True


def test_compute_totals_pairs_pentanomial_by_round_token_when_available() -> None:
    class _DummyDB:
        def get_games_with_players(self, *, game_type: str) -> list[dict[str, object]]:
            assert game_type == "arena"
            return [
                # Pair 1 (g0001, g0002): tested wins both -> WW
                {
                    "game_name": "g0001-a",
                    "black_player": "dev",
                    "white_player": "base",
                    "result": GameResult.BLACK_WIN,
                    "initial_sfen": "dup_sfen",
                },
                # Intentionally interleaved insertion order to stress pairing logic
                {
                    "game_name": "g0004-d",
                    "black_player": "base",
                    "white_player": "dev",
                    "result": GameResult.BLACK_WIN,
                    "initial_sfen": "dup_sfen",
                },
                # Pair 2 (g0003, g0004): tested loses both -> LL
                {
                    "game_name": "g0003-c",
                    "black_player": "dev",
                    "white_player": "base",
                    "result": GameResult.WHITE_WIN,
                    "initial_sfen": "dup_sfen",
                },
                {
                    "game_name": "g0002-b",
                    "black_player": "base",
                    "white_player": "dev",
                    "result": GameResult.WHITE_WIN,
                    "initial_sfen": "dup_sfen",
                },
            ]

    totals = _compute_totals(_DummyDB(), tested_engine="dev", base_engine="base")
    assert totals.wins == 2
    assert totals.losses == 2
    assert totals.draws == 0
    assert totals.ww == 1
    assert totals.ll == 1
    assert totals.dd == 0
    assert totals.dw == 0
    assert totals.ld == 0


def test_derive_worker_capabilities_has_public_fallback_and_private_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    import shogiarena._core.contexts.game_session.adapters.openbench.client_worker_capabilities as _worker_caps

    monkeypatch.setattr(_worker_caps.shutil, "which", lambda _name: None)
    compilers, tokens = _derive_worker_capabilities(
        {
            "PublicEngine": {
                "private": False,
                "compilers": ["gcc>=11", "clang>=14"],
            },
            "PrivateEngine": {
                "private": True,
            },
        },
        target_engines={"PublicEngine", "PrivateEngine"},
    )
    assert compilers["PublicEngine"][0] == "shogiarena"
    assert tokens["PrivateEngine"] is True


def _build_runner_for_openbench(tmp_path: Path, openbench: dict[str, object]) -> TournamentRunner:
    e1 = _engine_config_file(tmp_path, "dev")
    e2 = _engine_config_file(tmp_path, "base")
    cfg = TournamentRunConfig(
        experiment_name="openbench-runner-test",
        engines=[
            {"name": "dev", "engine_path": e1},
            {"name": "base", "engine_path": e2},
        ],
        sprt=SprtConfig(),
        openbench=openbench,
    )
    storage = FilesystemRunStorage(tmp_path / "run")
    from unittest.mock import AsyncMock, MagicMock

    from shogiarena._core.contexts.instances.ports.engine_factory import EngineFactoryService

    mock_factory = AsyncMock()
    engine_factory_service = EngineFactoryService(factory=mock_factory)
    return TournamentRunner(
        cfg,
        storage=storage,
        should_skip_resume=True,
        engine_factory_service=engine_factory_service,
        init_dashboard_html=MagicMock(),
        api_server_factory=MagicMock(),
    )


def _base_create_payload() -> dict[str, object]:
    return {
        "dev_engine": "RemoteDev",
        "base_engine": "RemoteBase",
        "dev_repo": "https://github.com/example/dev",
        "base_repo": "https://github.com/example/base",
        "dev_branch": "dev",
        "base_branch": "base",
        "dev_options": "Threads=1 Hash=64",
        "base_options": "Threads=1 Hash=64",
        "dev_time_control": "N=100000",
        "base_time_control": "N=100000",
        "book_name": "SomeBook",
        "scale_nps": 1000,
    }


def test_resolve_openbench_config_uses_settings_password_env_and_run_server_for_scale_nps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings_mod,
        "SETTINGS",
        replace(
            settings_mod.SETTINGS,
            openbench=OpenBenchSettings(
                server="https://settings.example.com",
                username="settings-user",
                password_env="OB_TEST_PASSWORD",
            ),
        ),
    )
    monkeypatch.delenv("OPENBENCH_PASSWORD", raising=False)
    monkeypatch.setenv("OB_TEST_PASSWORD", "secret-from-settings-env")

    runner = _build_runner_for_openbench(
        tmp_path,
        {
            "enabled": True,
            "mode": "create_test",
            "server": "https://run.example.com",
            "username": "run-user",
            "create": {
                "payload": {
                    "dev_engine": "RemoteDev",
                    "base_engine": "RemoteBase",
                    "dev_repo": "https://github.com/example/dev",
                    "base_repo": "https://github.com/example/base",
                    "dev_branch": "dev",
                    "base_branch": "base",
                    "dev_options": "Threads=1 Hash=64",
                    "base_options": "Threads=1 Hash=64",
                    "dev_time_control": "N=100000",
                    "base_time_control": "N=100000",
                    "book_name": "SomeBook",
                    "scale_nps": "auto",
                }
            },
        },
    )

    captured: dict[str, str] = {}

    def _fake_fetch_nps(
        engine_name: str,
        *,
        openbench_server: str,
        openbench_username: str,
        openbench_password: str,
    ) -> int:
        captured["engine_name"] = engine_name
        captured["server"] = openbench_server
        captured["username"] = openbench_username
        captured["password"] = openbench_password
        return 123456

    monkeypatch.setattr(_openbench_create_payload_builder_mod, "_fetch_engine_nps", _fake_fetch_nps)

    resolved = runner._openbench._resolve_config()
    assert resolved is not None
    assert resolved.server == "https://run.example.com"
    assert resolved.username == "run-user"
    assert resolved.password == "secret-from-settings-env"
    assert resolved.heartbeat_interval_sec == 30.0
    assert resolved.create_payload is not None
    assert resolved.create_payload["scale_nps"] == "123456"
    assert captured["server"] == "https://run.example.com"
    assert captured["username"] == "run-user"
    assert captured["engine_name"] == "RemoteBase"


def test_resolve_openbench_config_falls_back_book_name_when_none_not_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENBENCH_PASSWORD", "password")
    runner = _build_runner_for_openbench(
        tmp_path,
        {
            "enabled": True,
            "mode": "create_test",
            "server": "https://run.example.com",
            "username": "run-user",
            "create": {
                "payload": {
                    "dev_engine": "RemoteDev",
                    "base_engine": "RemoteBase",
                    "dev_repo": "https://github.com/example/dev",
                    "base_repo": "https://github.com/example/base",
                    "dev_branch": "dev",
                    "base_branch": "base",
                    "dev_options": "Threads=1 Hash=64",
                    "base_options": "Threads=1 Hash=64",
                    "dev_time_control": "N=100000",
                    "base_time_control": "N=100000",
                    "book_name": "NONE",
                    "scale_nps": 1000,
                }
            },
        },
    )
    monkeypatch.setattr(
        _openbench_create_payload_builder_mod,
        "_fetch_books",
        lambda **_kwargs: ["BookA", "BookB"],
    )
    resolved = runner._openbench._resolve_config()
    assert resolved is not None
    assert resolved.create_payload is not None
    assert resolved.create_payload["book_name"] == "BookA"


@pytest.mark.asyncio
async def test_openbench_client_submit_results_recovers_from_invalid_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    config = OpenBenchClientConfig(
        is_enabled=True,
        mode="existing_test",
        server="https://example.com",
        username="user",
        password="pass",
        target_test_id=42,
        submit_interval_games=1,
        is_strict=True,
        heartbeat_interval_sec=30.0,
        poll_interval_sec=1.0,
        assignment_timeout_sec=5.0,
        is_insecure_http_allowed=False,
        concurrency=1,
    )
    client = OpenBenchClient(config, tested_engine="dev", base_engine="base")
    client._machine_id = 1
    client._secret = "secret-1"
    client._workload_test_id = 42
    client._result_id = 10
    client._has_assignment = True

    submit_payloads: list[dict[str, str | int]] = []
    submit_calls = 0

    async def _fake_post(endpoint: str, payload: dict[str, object]) -> dict[str, object]:
        nonlocal submit_calls
        if endpoint != "clientSubmitResults":
            return {}
        submit_calls += 1
        submit_payloads.append(dict(payload))
        if submit_calls == 1:
            return {"error": "Invalid Secret Token"}
        return {"stop": False}

    register_calls = 0
    claim_calls = 0

    async def _fake_register_worker() -> None:
        nonlocal register_calls
        register_calls += 1
        client._machine_id = 2
        client._secret = "secret-2"

    async def _fake_claim_target_workload() -> None:
        nonlocal claim_calls
        claim_calls += 1
        client._workload_test_id = 42
        client._result_id = 11
        client._has_assignment = True

    monkeypatch.setattr(client, "_post", _fake_post)
    monkeypatch.setattr(client, "_register_worker", _fake_register_worker)
    monkeypatch.setattr(client, "_claim_target_workload", _fake_claim_target_workload)

    should_stop = await client._submit_results(OpenBenchCounters(wins=1))
    assert should_stop is False
    assert register_calls == 1
    assert claim_calls == 1
    assert submit_payloads[0]["machine_id"] == 1
    assert submit_payloads[1]["machine_id"] == 2
    assert submit_payloads[1]["result_id"] == 11


@pytest.mark.asyncio
async def test_openbench_client_heartbeat_recovers_from_bad_machine_id(monkeypatch: pytest.MonkeyPatch) -> None:
    config = OpenBenchClientConfig(
        is_enabled=True,
        mode="existing_test",
        server="https://example.com",
        username="user",
        password="pass",
        target_test_id=42,
        submit_interval_games=1,
        is_strict=True,
        heartbeat_interval_sec=30.0,
        poll_interval_sec=1.0,
        assignment_timeout_sec=5.0,
        is_insecure_http_allowed=False,
        concurrency=1,
    )
    client = OpenBenchClient(config, tested_engine="dev", base_engine="base")
    client._machine_id = 1
    client._secret = "secret-1"
    client._workload_test_id = 42
    client._result_id = 10
    client._has_assignment = True

    heartbeat_calls = 0

    async def _fake_post(endpoint: str, payload: dict[str, object]) -> dict[str, object]:
        nonlocal heartbeat_calls
        if endpoint != "clientHeartbeat":
            return {}
        heartbeat_calls += 1
        if heartbeat_calls == 1:
            return {"error": "Bad Machine Id"}
        return {"stop": True}

    register_calls = 0
    claim_calls = 0

    async def _fake_register_worker() -> None:
        nonlocal register_calls
        register_calls += 1
        client._machine_id = 3
        client._secret = "secret-3"

    async def _fake_claim_target_workload() -> None:
        nonlocal claim_calls
        claim_calls += 1
        client._workload_test_id = 42
        client._result_id = 12
        client._has_assignment = True

    monkeypatch.setattr(client, "_post", _fake_post)
    monkeypatch.setattr(client, "_register_worker", _fake_register_worker)
    monkeypatch.setattr(client, "_claim_target_workload", _fake_claim_target_workload)

    should_stop = await client.should_stop_after_heartbeat()
    assert should_stop is True
    assert heartbeat_calls == 2
    assert register_calls == 1
    assert claim_calls == 1


@pytest.mark.asyncio
async def test_tournament_runner_openbench_init_continues_when_strict_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENBENCH_PASSWORD", "password")
    runner = _build_runner_for_openbench(
        tmp_path,
        {
            "enabled": True,
            "mode": "existing_test",
            "strict": False,
            "server": "https://run.example.com",
            "username": "run-user",
            "target_test_id": 1,
        },
    )

    async def _fail_initialize(self: OpenBenchClient) -> None:
        raise OpenBenchError("init failed")

    monkeypatch.setattr(OpenBenchClient, "initialize", _fail_initialize)

    class _MockStopController:
        def request_stop(self, *, reason: str = "") -> None:
            pass

    await runner._openbench.init(stop_controller=_MockStopController())
    assert runner._openbench.client is None


def test_openbench_create_payload_rejects_invalid_upload_pgns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBENCH_PASSWORD", "password")
    payload = _base_create_payload()
    payload["upload_pgns"] = "MAYBE"
    runner = _build_runner_for_openbench(
        tmp_path,
        {
            "enabled": True,
            "mode": "create_test",
            "server": "https://run.example.com",
            "username": "run-user",
            "create": {"payload": payload},
        },
    )
    with pytest.raises(ValueError, match="upload_pgns"):
        runner._openbench._resolve_config()


def test_openbench_create_payload_rejects_invalid_scale_method(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBENCH_PASSWORD", "password")
    payload = _base_create_payload()
    payload["scale_method"] = "UNKNOWN"
    runner = _build_runner_for_openbench(
        tmp_path,
        {
            "enabled": True,
            "mode": "create_test",
            "server": "https://run.example.com",
            "username": "run-user",
            "create": {"payload": payload},
        },
    )
    with pytest.raises(ValueError, match="scale_method"):
        runner._openbench._resolve_config()


def test_openbench_create_payload_rejects_invalid_sprt_bounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBENCH_PASSWORD", "password")
    payload = _base_create_payload()
    payload["test_mode"] = "SPRT"
    payload["test_bounds"] = "bad-format"
    runner = _build_runner_for_openbench(
        tmp_path,
        {
            "enabled": True,
            "mode": "create_test",
            "server": "https://run.example.com",
            "username": "run-user",
            "create": {"payload": payload},
        },
    )
    with pytest.raises(ValueError, match="test_bounds"):
        runner._openbench._resolve_config()


def test_openbench_create_payload_rejects_out_of_range_sprt_confidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENBENCH_PASSWORD", "password")
    payload = _base_create_payload()
    payload["test_mode"] = "SPRT"
    payload["test_confidence"] = "[0.0, 1.5]"
    runner = _build_runner_for_openbench(
        tmp_path,
        {
            "enabled": True,
            "mode": "create_test",
            "server": "https://run.example.com",
            "username": "run-user",
            "create": {"payload": payload},
        },
    )
    with pytest.raises(ValueError, match="test_confidence"):
        runner._openbench._resolve_config()
