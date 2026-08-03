from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from aiohttp import ClientResponse
from aiohttp.test_utils import TestClient, TestServer

from shogiarena._core.contexts.spsa.adapters.ledger_store import open_spsa_ledger
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory


def _write_revision_archive(run_dir: Path, *, revision: int) -> Path:
    db_path = run_dir / "game.db"
    repository = SQLiteShogiDBFactory(db_path).create()
    repository.create_tables()
    repository.close_db()
    with open_spsa_ledger(run_dir) as ledger:
        ledger.connection.execute(
            """
            INSERT INTO run_contract (
                run_id, contract_schema, resume_hash, space_digest, rng_schema,
                sealed_run_seed, status, contract_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("run-feed", "v1", "resume", "space", "rng", "seed", "running", "{}", "t0", "t0"),
        )
        ledger.connection.execute(
            """
            INSERT INTO event_revisions (run_id, revision, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("run-feed", revision, "run_started", "{}", "2026-01-01T00:00:00+00:00"),
        )
        ledger.connection.commit()
    (run_dir / "spsa" / "meta.json").write_text('{"type":"spsa"}', encoding="utf-8")
    return db_path


def _build_client(run_dir: Path, db_path: Path) -> TestClient:
    server = build_default_root().api_server_factory(
        db_path=db_path,
        port=8080,
        run_dir=run_dir,
        instance_pool=None,
        read_only=False,
        dashboard_num_workers=1,
        dashboard_profiles=("spsa",),
    )
    return TestClient(TestServer(server.app))


async def _read_event(response: ClientResponse) -> tuple[int, dict[str, object]]:
    chunk = await asyncio.wait_for(response.content.readuntil(b"\n\n"), timeout=3.0)
    lines = chunk.decode().splitlines()
    event_id = int(next(line.removeprefix("id: ") for line in lines if line.startswith("id: ")))
    payload = json.loads(next(line.removeprefix("data: ") for line in lines if line.startswith("data: ")))
    return event_id, payload


@pytest.mark.asyncio
async def test_revision_feed_multi_client_restart_and_gap_recovery(tmp_path: Path) -> None:
    db_path = _write_revision_archive(tmp_path, revision=7)

    async with _build_client(tmp_path, db_path) as client:
        removed_routes = [
            "/ws/spsa/updates",
            "/api/spsa/ltc/games/stream",
            "/api/spsa/ltc/results/stream",
            "/api/spsa/ltc/progress/stream",
            "/api/spsa/updates/stream",
            "/api/spsa/update/detail/stream",
            "/api/spsa/variant/games/stream",
            "/api/spsa/analysis/correlation/stream",
            "/api/spsa/summary/stream",
        ]
        assert [int((await client.get(route)).status) for route in removed_routes] == [404] * len(removed_routes)
        convergence = await client.get("/api/spsa/analysis/convergence")
        assert convergence.status == 200
        assert convergence.content_type == "application/json"
        first, second = await asyncio.gather(
            client.get("/api/spsa/revisions/stream?poll_interval=0.2"),
            client.get(
                "/api/spsa/revisions/stream?poll_interval=0.2",
                headers={"Last-Event-ID": "1"},
            ),
        )
        first_id, first_payload = await _read_event(first)
        second_id, second_payload = await _read_event(second)
        first.close()
        second.close()

    assert first_id == second_id == 7
    assert first_payload["seq"] == second_payload["seq"] == 7
    assert first_payload["data"] == {
        "run_id": "run-feed",
        "revision": 7,
        "data_generation": 1,
        "gap_detected": False,
        "snapshot_required": True,
        "snapshot_url": "/api/spsa/summary",
        "replay_supported": False,
        "terminal": False,
    }
    assert second_payload["resume_from"] == 1
    assert second_payload["data"]["gap_detected"] is True  # type: ignore[index]

    async with _build_client(tmp_path, db_path) as restarted_client:
        restarted = await restarted_client.get(
            "/api/spsa/revisions/stream?poll_interval=0.2",
            headers={"Last-Event-ID": "7"},
        )
        restarted_id, restarted_payload = await _read_event(restarted)
        restarted.close()

    assert restarted_id == 7
    assert restarted_payload["seq"] == 7
    assert restarted_payload["resume_from"] == 7
    assert restarted_payload["data"]["gap_detected"] is False  # type: ignore[index]


@pytest.mark.asyncio
async def test_revision_feed_emits_new_durable_revision(tmp_path: Path) -> None:
    db_path = _write_revision_archive(tmp_path, revision=1)

    async with _build_client(tmp_path, db_path) as client:
        response = await client.get("/api/spsa/revisions/stream?poll_interval=0.2")
        first_id, _ = await _read_event(response)
        with open_spsa_ledger(tmp_path) as ledger:
            ledger.connection.execute(
                """
                INSERT INTO event_revisions (run_id, revision, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("run-feed", 2, "update_committed", "{}", "2026-01-01T00:00:01+00:00"),
            )
            ledger.connection.commit()
        second_id, second_payload = await _read_event(response)
        response.close()

    assert first_id == 1
    assert second_id == 2
    assert second_payload["seq"] == 2
    assert second_payload["data"]["gap_detected"] is False  # type: ignore[index]


@pytest.mark.asyncio
async def test_revision_feed_closes_after_terminal_revision(tmp_path: Path) -> None:
    db_path = _write_revision_archive(tmp_path, revision=1)
    with open_spsa_ledger(tmp_path) as ledger:
        ledger.connection.execute("UPDATE run_contract SET status = 'terminal' WHERE run_id = 'run-feed'")
        ledger.connection.execute(
            """
            INSERT INTO event_revisions (run_id, revision, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("run-feed", 2, "terminal", "{}", "2026-01-01T00:00:01+00:00"),
        )
        ledger.connection.commit()

    async with _build_client(tmp_path, db_path) as client:
        response = await client.get("/api/spsa/revisions/stream?poll_interval=0.2")
        event_id, payload = await _read_event(response)
        eof = await asyncio.wait_for(response.content.read(), timeout=3.0)

    assert event_id == 2
    assert payload["data"]["terminal"] is True  # type: ignore[index]
    assert eof == b""


@pytest.mark.asyncio
async def test_revision_feed_emits_when_only_projected_data_changes(tmp_path: Path) -> None:
    """LTC 無効の run では ``event_revisions`` が増えないまま対局と update が進む。

    durable revision だけを配信条件にしていると、この間 dashboard は
    Updates を取り直す契機を得られず、起動時のスナップショットのまま止まる。
    """

    db_path = _write_revision_archive(tmp_path, revision=1)

    async with _build_client(tmp_path, db_path) as client:
        response = await client.get("/api/spsa/revisions/stream?poll_interval=0.2")
        first_id, first_payload = await _read_event(response)

        # An update lands, but nothing is appended to `event_revisions`.
        with open_spsa_ledger(tmp_path) as ledger:
            ledger.connection.execute(
                """
                INSERT INTO updates (
                    run_id, update_idx, state, theta_before_json, schedule_json,
                    ltc_required, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("run-feed", 1, "PLANNED", "{}", "{}", 0, 1, "t0", "2026-01-01T00:00:02+00:00"),
            )
            ledger.connection.commit()

        second_id, second_payload = await _read_event(response)
        response.close()

    assert first_payload["data"]["revision"] == 1  # type: ignore[index]
    # The durable revision is unchanged, so only the projected data version moved.
    assert second_payload["data"]["revision"] == 1  # type: ignore[index]
    assert second_payload["data"]["data_generation"] > first_payload["data"]["data_generation"]  # type: ignore[index]
    assert second_id == first_id
