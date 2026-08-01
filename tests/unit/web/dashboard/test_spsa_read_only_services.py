from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from shogiarena._core.contexts.dashboard.adapters.spsa.analysis_service import SpsaAnalysisService
from shogiarena._core.contexts.dashboard.adapters.spsa.params_service import SpsaParamsService
from shogiarena._core.contexts.dashboard.adapters.spsa.service_factory import SpsaDashboardServicesFactory
from shogiarena._core.contexts.dashboard.adapters.spsa.summary_service import SpsaSummaryService
from shogiarena._core.contexts.dashboard.adapters.spsa.update_query_service import SpsaUpdateQueryService
from shogiarena._core.contexts.dashboard.application.spsa.data_store import SpsaStore
from shogiarena._core.contexts.dashboard.ports.spsa_service_ports import SpsaUpdateNotFoundError
from shogiarena._core.contexts.spsa.adapters.ledger_store import SpsaLedgerSchemaError, open_spsa_ledger
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory


def _tree_identity(root: Path) -> dict[str, tuple[str, int, int, str]]:
    identity: dict[str, tuple[str, int, int, str]] = {}
    for path in (root, *root.rglob("*")):
        relative = "." if path == root else path.relative_to(root).as_posix()
        metadata = path.stat()
        if path.is_file():
            identity[relative] = (
                "file",
                metadata.st_size,
                metadata.st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        elif path.is_dir():
            identity[relative] = ("directory", metadata.st_size, metadata.st_mtime_ns, "")
    return identity


@contextmanager
def _non_writable_archive(root: Path) -> Iterator[None]:
    if os.name == "nt":
        original_modes = {path: stat.S_IMODE(path.stat().st_mode) for path in root.rglob("*") if path.is_file()}
        for path in original_modes:
            path.chmod(stat.S_IREAD)
        identity = subprocess.run(
            ["whoami"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        acl_applied = False
        try:
            subprocess.run(
                ["icacls", str(root), "/deny", f"{identity}:(W)"],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            acl_applied = True
            yield
        finally:
            if acl_applied:
                subprocess.run(
                    ["icacls", str(root), "/remove:d", identity],
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
            for path, mode in original_modes.items():
                path.chmod(mode)
        return

    original_modes = {path: stat.S_IMODE(path.stat().st_mode) for path in (root, *root.rglob("*"))}
    for path in sorted(original_modes, key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o444 if path.is_file() else 0o555)
    try:
        yield
    finally:
        for path in sorted(original_modes, key=lambda item: len(item.parts)):
            path.chmod(original_modes[path])


def _write_spsa_archive(run_dir: Path) -> Path:
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
            ("run-1", "v1", "resume", "space", "rng", "seed", "running", "{}", "t0", "t0"),
        )
        ledger.connection.commit()
    spsa_dir = run_dir / "spsa"
    (spsa_dir / "meta.json").write_text(
        json.dumps(
            {
                "type": "spsa",
                "experiment_name": "read-only",
                "session_uuid": "session-1",
                "num_updates": 1,
                "initial_params": {"p": 1.0},
            }
        ),
        encoding="utf-8",
    )
    (spsa_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "event": "update",
                "session_uuid": "session-1",
                "update_idx": 1,
                "params": {"p": 2.0},
                "ts": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (spsa_dir / "space.normalized.json").write_text(
        json.dumps(
            {
                "schema_version": "shogiarena.spsa.space.v1",
                "target": {
                    "engine_family": "test",
                    "protocol": "usi_options",
                    "required_options_policy": "strict",
                    "tunable_manifest": {"required": False, "command": "usi_tunables"},
                },
                "parameters": [
                    {
                        "id": "p",
                        "target": {"option": "p", "value_encoding": "decimal"},
                        "value_type": "float",
                        "initial": 1.0,
                        "bounds": {"min": 0.0, "max": 3.0},
                        "schedule": {"c_end": 1.0, "r_end": 0.1},
                        "rounding": {"mode": "none"},
                        "significant_digits": 9,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return db_path


def _commit_ledger_update_newer_than_derived_files(run_dir: Path) -> None:
    timestamp = "2026-01-01T00:00:00+00:00"
    with open_spsa_ledger(run_dir) as ledger:
        ledger.connection.execute(
            """
            INSERT INTO updates (
                run_id, update_idx, state, theta_before_json, theta_candidate_json,
                theta_final_json, schedule_json, ltc_required, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-1",
                1,
                "COMMITTED",
                '{"p":1.0}',
                '{"p":3.0}',
                '{"p":3.0}',
                '{"step":0.25,"delta_norm":2.0}',
                0,
                2,
                timestamp,
                timestamp,
            ),
        )
        ledger.connection.execute(
            """
            INSERT INTO pair_assignments (
                run_id, update_idx, pair_id, assignment_kind, assignment_schema,
                assignment_digest, opening_json, color_assignment_json, flip_json,
                rounding_samples_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-1",
                1,
                "pair-1",
                "SPSA",
                "v1",
                "digest",
                "{}",
                '{"games":[{"game_id":"ledger-game","tuned_as":"black"}]}',
                "{}",
                "{}",
                timestamp,
            ),
        )
        ledger.connection.execute(
            """
            INSERT INTO game_observations (
                run_id, game_id, update_idx, pair_id, attempt_id, observation_kind,
                result_kind, game_db_id, evidence_digest, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("run-1", "ledger-game", 1, "pair-1", "attempt-1", "SPSA", "DRAW", 1, "digest", timestamp),
        )
        ledger.connection.commit()


def test_read_only_spsa_services_leave_archive_tree_unchanged(tmp_path: Path) -> None:
    db_path = _write_spsa_archive(tmp_path)
    before = _tree_identity(tmp_path)

    services = SpsaDashboardServicesFactory().create_services(
        run_dir=tmp_path,
        db_path=db_path,
        read_only=True,
    )
    summary = services.summary_service.compute_summary()
    games, total = services.game_listing_service.list_games(0, 10, "")

    assert summary["experiment_name"] == "read-only"
    assert games == []
    assert total == 0
    assert _tree_identity(tmp_path) == before
    assert not (tmp_path / "spsa" / ".cache").exists()


def test_summary_ignores_deletable_or_corrupt_legacy_cache(tmp_path: Path) -> None:
    db_path = _write_spsa_archive(tmp_path)
    cache_path = tmp_path / "spsa" / ".cache" / "summary_cache_v1.json"
    cache_path.parent.mkdir()
    cache_path.write_text("{corrupt", encoding="utf-8")
    before = _tree_identity(tmp_path)

    first = (
        SpsaDashboardServicesFactory()
        .create_services(
            run_dir=tmp_path,
            db_path=db_path,
            read_only=True,
        )
        .summary_service.compute_summary()
    )

    assert first["experiment_name"] == "read-only"
    assert _tree_identity(tmp_path) == before
    cache_path.unlink()
    cache_path.parent.rmdir()

    second = (
        SpsaDashboardServicesFactory()
        .create_services(
            run_dir=tmp_path,
            db_path=db_path,
            read_only=True,
        )
        .summary_service.compute_summary()
    )

    assert second == first


def test_resume_preserves_historical_dashboard_projection(tmp_path: Path) -> None:
    db_path = _write_spsa_archive(tmp_path)
    spsa_dir = tmp_path / "spsa"
    meta_path = spsa_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "session_uuid": "session-1",
            "experiment_initial_params": {"p": 1.0},
            "session_start_params": {"p": 1.0},
            "sessions": [
                {
                    "session_uuid": "session-1",
                    "session_started_at": "2026-01-01T00:00:00Z",
                    "session_start_params": {"p": 1.0},
                }
            ],
        }
    )
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    events_path = spsa_dir / "events.jsonl"
    events_path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "event": "game_result",
                        "session_uuid": "session-1",
                        "update_idx": 1,
                        "winner": 1,
                        "game_id": "pre-resume-game",
                        "ts": 1,
                    }
                ),
                json.dumps(
                    {
                        "event": "update",
                        "session_uuid": "session-1",
                        "update_idx": 1,
                        "params": {"p": 1.5},
                        "step": 0.5,
                        "ts": 2,
                    }
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    store = SpsaStore(run_dir=tmp_path)
    query = SpsaUpdateQueryService(store=store, db_path=db_path, read_only=True)
    before_detail = query.build_update_detail(1)
    before_params = SpsaParamsService(store=store, run_dir=tmp_path).build_params_payload()
    before_analysis = SpsaAnalysisService(store=store).compute_convergence_analysis(query.collect_updates_from_events())

    meta["session_uuid"] = "session-2"
    meta["session_start_params"] = {"p": 1.5}
    meta["sessions"].append(
        {
            "session_uuid": "session-2",
            "session_started_at": "2026-01-02T00:00:00Z",
            "session_start_params": {"p": 1.5},
        }
    )
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    resumed_store = SpsaStore(run_dir=tmp_path)
    resumed_query = SpsaUpdateQueryService(store=resumed_store, db_path=db_path, read_only=True)
    resumed_summary = SpsaSummaryService(resumed_store).compute_summary()
    resumed_detail = resumed_query.build_update_detail(1)
    resumed_params = SpsaParamsService(store=resumed_store, run_dir=tmp_path).build_params_payload()
    resumed_analysis = SpsaAnalysisService(store=resumed_store).compute_convergence_analysis(
        resumed_query.collect_updates_from_events()
    )

    assert resumed_summary["wins"] == 1
    assert resumed_summary["current_session_uuid"] == "session-2"
    assert len(resumed_summary["resume_boundaries"]) == 2
    assert resumed_query.collect_game_id_entries() == [("pre-resume-game", 1)]
    assert resumed_detail == before_detail
    assert resumed_params == before_params
    assert resumed_analysis == before_analysis


def test_dashboard_reconstructs_newer_ledger_when_derived_json_is_stale(tmp_path: Path) -> None:
    db_path = _write_spsa_archive(tmp_path)
    _commit_ledger_update_newer_than_derived_files(tmp_path)
    (tmp_path / "spsa" / "events.jsonl").write_text(
        '{"event":"update","update_idx":1,"params":{"p":2.0},"ts":1}\n',
        encoding="utf-8",
    )
    index_path = tmp_path / "spsa" / "index.json"
    assert not index_path.exists()

    services = SpsaDashboardServicesFactory().create_services(
        run_dir=tmp_path,
        db_path=db_path,
        read_only=True,
    )

    summary = services.summary_service.compute_summary()
    updates = services.update_query_service.load_index_updates()
    detail = services.update_query_service.build_update_detail(1)
    events = services.update_query_service.load_event_entries()

    assert (summary["wins"], summary["losses"], summary["draws"]) == (0, 0, 1)
    assert summary["games"] == {"completed": 1, "total": 1}
    assert updates[0]["params"] == {"p": 3.0}
    assert detail["params"] == {"p": 3.0}
    assert [game["game_id"] for game in detail["games"]] == ["ledger-game"]
    assert any(event.get("game_id") == "ledger-game" and event.get("winner") == 2 for event in events)
    services.update_query_service.close()


@pytest.mark.asyncio
async def test_archived_rest_api_prefers_newer_ledger_over_stale_derived_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = _write_spsa_archive(tmp_path)
    _commit_ledger_update_newer_than_derived_files(tmp_path)
    (tmp_path / "spsa" / "events.jsonl").write_text(
        '{"event":"update","update_idx":1,"params":{"p":2.0},"ts":1}\n',
        encoding="utf-8",
    )

    def _reject_derived_read(_store: SpsaStore) -> list[object]:
        raise AssertionError("derived SPSA history must not be read when the ledger exists")

    monkeypatch.setattr(SpsaStore, "load_event_entries", _reject_derived_read)
    monkeypatch.setattr(SpsaStore, "load_index_updates", _reject_derived_read)
    monkeypatch.setattr(SpsaStore, "load_ltc_results", _reject_derived_read)
    server = build_default_root().api_server_factory(
        db_path=db_path,
        port=8080,
        run_dir=tmp_path,
        instance_pool=None,
        read_only=True,
        dashboard_num_workers=1,
        dashboard_profiles=("spsa",),
    )

    async with TestClient(TestServer(server.app)) as client:
        summary = await (await client.get("/api/spsa/summary")).json()
        updates = await (await client.get("/api/spsa/updates")).json()
        detail = await (await client.get("/api/spsa/update/1?view=full")).json()
        events = await (await client.get("/api/spsa/events")).json()
        correlation = await (await client.get("/api/spsa/analysis/correlation")).json()
        convergence = await (await client.get("/api/spsa/analysis/convergence?format=json")).json()

    assert (summary["wins"], summary["losses"], summary["draws"]) == (0, 0, 1)
    assert updates["updates"][0]["params"] == {"p": 3.0}
    assert detail["params"] == {"p": 3.0}
    assert detail["games"][0]["game_id"] == "ledger-game"
    assert any(event.get("game_id") == "ledger-game" for event in events["events"])
    assert correlation["status"] == "ready"
    assert convergence["status"] == "ready"


def test_read_only_spsa_factory_rejects_archive_without_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "game.db"
    repository = SQLiteShogiDBFactory(db_path).create()
    repository.create_tables()
    repository.close_db()
    spsa_dir = tmp_path / "spsa"
    spsa_dir.mkdir()
    (spsa_dir / "meta.json").write_text('{"type":"spsa"}', encoding="utf-8")

    with pytest.raises(SpsaLedgerSchemaError, match="does not exist for read-only open"):
        SpsaDashboardServicesFactory().create_services(
            run_dir=tmp_path,
            db_path=db_path,
            read_only=True,
        )


def test_update_detail_uses_ledger_identity_for_pending_and_not_found(tmp_path: Path) -> None:
    db_path = _write_spsa_archive(tmp_path)
    with open_spsa_ledger(tmp_path) as ledger:
        ledger.connection.execute(
            """
            INSERT INTO updates (
                run_id, update_idx, state, theta_before_json, schedule_json,
                ltc_required, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-1",
                1,
                "PLANNED",
                '{"p":1.0}',
                "{}",
                0,
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        ledger.connection.commit()

    services = SpsaDashboardServicesFactory().create_services(
        run_dir=tmp_path,
        db_path=db_path,
        read_only=True,
    )
    detail = services.update_query_service.build_update_detail(1)

    assert detail["run_id"] == "run-1"
    assert detail["ledger_state"] == "PLANNED"
    assert detail["is_pending"] is True
    with pytest.raises(SpsaUpdateNotFoundError, match="not planned"):
        services.update_query_service.build_update_detail(2)


@pytest.mark.asyncio
async def test_read_only_spsa_rest_apis_leave_archive_tree_unchanged(tmp_path: Path) -> None:
    db_path = _write_spsa_archive(tmp_path)
    before = _tree_identity(tmp_path)
    endpoints = [
        "/api/spsa/events",
        "/api/spsa/summary",
        "/api/spsa/params",
        "/api/spsa/update/1",
        "/api/spsa/updates",
        "/api/spsa/variants",
        "/api/spsa/analysis/correlation",
        "/api/spsa/analysis/convergence?format=json",
        "/api/spsa/games",
        "/api/spsa/game/missing",
        "/api/spsa/ltc/summary",
        "/api/spsa/ltc/results",
    ]

    with _non_writable_archive(tmp_path):
        with pytest.raises(OSError):
            (tmp_path / "write-probe").write_text("blocked", encoding="utf-8")
        server = build_default_root().api_server_factory(
            db_path=db_path,
            port=8080,
            run_dir=tmp_path,
            instance_pool=None,
            read_only=True,
            dashboard_num_workers=2,
            dashboard_profiles=("spsa",),
        )
        async with TestClient(TestServer(server.app)) as client:
            statuses = [(await client.get(endpoint)).status for endpoint in endpoints]
            index_response = await client.get("/index.html")
            index_html = await index_response.text()
            static_response = await client.get("/static/build-meta.json")
            traversal_response = await client.get("/static/%2e%2e/%2e%2e/archive-escape")
            removed_summary_stream = await client.get("/api/spsa/summary/stream?poll_interval=0.5")
            revision_response = await client.get("/api/spsa/revisions/stream?poll_interval=0.2")
            revision_event = await asyncio.wait_for(revision_response.content.readuntil(b"\n\n"), timeout=3.0)
            revision_response.close()

    assert all(status in {200, 404} for status in statuses)
    assert index_response.status == 200
    assert 'data-dashboard-profile="spsa"' in index_html
    assert "window.__ARENA_NUM_WORKERS__ = 2" in index_html
    assert "data/arena_port.js" not in index_html
    assert static_response.status == 200
    assert traversal_response.status in {400, 404}
    assert removed_summary_stream.status == 404
    assert b"event: spsa_revision" in revision_event
    assert _tree_identity(tmp_path) == before
