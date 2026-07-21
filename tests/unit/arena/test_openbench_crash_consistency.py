"""OpenBench additive result submission crash-consistency regressions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from shogiarena._core.contexts.game_session.adapters.openbench.client import OpenBenchClient
from shogiarena._core.contexts.game_session.adapters.openbench.client_types import (
    OpenBenchClientConfig,
    OpenBenchError,
)
from shogiarena._core.shared.kernel.game_results import GameResult


class _OneGameDatabase:
    def get_games_with_players(self, *, game_type: str) -> list[dict[str, Any]]:
        assert game_type == "arena"
        return [
            {
                "game_name": "g0001-a",
                "black_player": "dev",
                "white_player": "base",
                "result": GameResult.BLACK_WIN,
                "initial_sfen": "startpos",
            }
        ]


def _assigned_client(*, is_strict: bool = True) -> OpenBenchClient:
    client = OpenBenchClient(
        OpenBenchClientConfig(
            is_enabled=True,
            mode="existing_test",
            server="https://example.com",
            username="user",
            password="password",
            target_test_id=42,
            submit_interval_games=1,
            is_strict=is_strict,
            heartbeat_interval_sec=30.0,
            poll_interval_sec=1.0,
            assignment_timeout_sec=5.0,
            is_insecure_http_allowed=False,
            concurrency=1,
        ),
        tested_engine="dev",
        base_engine="base",
    )
    client._machine_id = 1
    client._secret = "secret"
    client._workload_test_id = 42
    client._result_id = 7
    client._has_assignment = True
    return client


@pytest.mark.asyncio
async def test_openbench_does_not_send_when_write_ahead_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _assigned_client()
    post_calls = 0

    async def _fake_post(_endpoint: str, _payload: dict[str, object]) -> dict[str, object]:
        nonlocal post_calls
        post_calls += 1
        return {"stop": False}

    def _fail_persist() -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(client, "_post", _fake_post)

    with pytest.raises(OpenBenchError, match="submission intent"):
        await client.try_sync(_OneGameDatabase(), persist_state=_fail_persist)

    assert post_calls == 0
    assert client.snapshot_state().get("inflight_submission") is None


@pytest.mark.asyncio
async def test_openbench_response_loss_preserves_ambiguity_and_disables_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _assigned_client(is_strict=False)
    persisted: list[dict[str, object]] = []
    post_calls = 0

    async def _lose_response(_endpoint: str, _payload: dict[str, object]) -> dict[str, object]:
        nonlocal post_calls
        post_calls += 1
        raise OpenBenchError("connection closed after request upload")

    monkeypatch.setattr(client, "_post", _lose_response)

    with pytest.raises(OpenBenchError, match="connection closed"):
        await client.try_sync(
            _OneGameDatabase(),
            persist_state=lambda: persisted.append(deepcopy(client.snapshot_state())),
        )

    assert post_calls == 1
    assert persisted[0]["inflight_submission"] is not None
    assert client.snapshot_state()["inflight_submission"] is not None

    with pytest.raises(OpenBenchError, match="automatic retry is disabled"):
        await client.try_sync(_OneGameDatabase(), persist_state=lambda: None)
    assert post_calls == 1

    resumed = _assigned_client(is_strict=False)
    with pytest.raises(OpenBenchError, match="ambiguous in-flight result submission"):
        resumed.restore_state(persisted[0])


@pytest.mark.asyncio
async def test_openbench_acknowledged_submission_durably_clears_write_ahead_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _assigned_client()
    persisted: list[dict[str, object]] = []

    async def _acknowledge(_endpoint: str, _payload: dict[str, object]) -> dict[str, object]:
        return {"stop": False}

    monkeypatch.setattr(client, "_post", _acknowledge)

    should_stop = await client.try_sync(
        _OneGameDatabase(),
        persist_state=lambda: persisted.append(deepcopy(client.snapshot_state())),
    )

    assert should_stop is False
    assert len(persisted) == 2
    assert persisted[0]["inflight_submission"] is not None
    assert persisted[0]["submitted"]["wins"] == 0
    assert persisted[1].get("inflight_submission") is None
    assert persisted[1]["submitted"]["wins"] == 1

    restored = _assigned_client()
    restored.restore_state(persisted[1])
    assert restored.snapshot_state()["submitted"]["wins"] == 1


@pytest.mark.asyncio
async def test_openbench_ack_persistence_failure_keeps_ambiguity_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _assigned_client(is_strict=False)
    persist_calls = 0

    async def _acknowledge(_endpoint: str, _payload: dict[str, object]) -> dict[str, object]:
        return {"stop": False}

    def _persist_then_fail_ack() -> None:
        nonlocal persist_calls
        persist_calls += 1
        if persist_calls == 2:
            raise OSError("ack state write failed")

    monkeypatch.setattr(client, "_post", _acknowledge)

    with pytest.raises(OpenBenchError, match="submission acknowledgement"):
        await client.try_sync(_OneGameDatabase(), persist_state=_persist_then_fail_ack)

    snapshot = client.snapshot_state()
    assert snapshot["inflight_submission"] is not None
    assert snapshot["submitted"]["wins"] == 0

    with pytest.raises(OpenBenchError, match="automatic retry is disabled"):
        await client.try_sync(_OneGameDatabase(), persist_state=lambda: None)


@pytest.mark.asyncio
async def test_openbench_recovery_persists_new_result_identity_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _assigned_client()
    persisted: list[dict[str, object]] = []
    post_calls = 0

    async def _post_then_lose_retry(_endpoint: str, _payload: dict[str, object]) -> dict[str, object]:
        nonlocal post_calls
        post_calls += 1
        if post_calls == 1:
            return {"error": "Invalid Secret Token"}
        raise OpenBenchError("retry response lost")

    async def _register_worker() -> None:
        client._machine_id = 2
        client._secret = "replacement-secret"

    async def _claim_workload() -> None:
        client._workload_test_id = 42
        client._result_id = 99
        client._has_assignment = True

    monkeypatch.setattr(client, "_post", _post_then_lose_retry)
    monkeypatch.setattr(client, "_register_worker", _register_worker)
    monkeypatch.setattr(client, "_claim_target_workload", _claim_workload)

    with pytest.raises(OpenBenchError, match="retry response lost"):
        await client.try_sync(
            _OneGameDatabase(),
            persist_state=lambda: persisted.append(deepcopy(client.snapshot_state())),
        )

    assert len(persisted) == 2
    assert persisted[0]["result_id"] == 7
    assert persisted[1]["result_id"] == 99
    assert persisted[1]["inflight_submission"] is not None
