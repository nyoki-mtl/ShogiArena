"""Benchmark the Phase 8 SPSA dashboard scale contract."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Final

from aiohttp import ClientResponse
from aiohttp.test_utils import TestClient, TestServer

from shogiarena._core.contexts.dashboard.adapters.spsa.game_listing_service import SpsaGameListingService
from shogiarena._core.contexts.dashboard.adapters.spsa.run_projector import SpsaRunProjector
from shogiarena._core.contexts.dashboard.adapters.spsa.update_query_service import SpsaUpdateQueryService
from shogiarena._core.contexts.dashboard.application.spsa.data_store import SpsaStore
from shogiarena._core.contexts.spsa.adapters.ledger_store import open_spsa_ledger
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory
from shogiarena._core.shared.kernel.json_types import JsonObject

THRESHOLDS: Final[dict[str, float]] = {
    "projection_1k_ms": 1_000.0,
    "projection_10k_ms": 5_000.0,
    "single_revision_p95_ms": 50.0,
    "detail_p95_ms": 20.0,
    "game_list_p95_ms": 100.0,
    "event_loop_stall_ms": 25.0,
    "idle_sse_cpu_ms": 150.0,
}


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return ordered[index]


def _seed_ledger(run_dir: Path, *, updates: int) -> None:
    now = "2026-01-01T00:00:00+00:00"
    with open_spsa_ledger(run_dir) as ledger:
        ledger.connection.execute(
            """
            INSERT INTO run_contract (
                run_id, contract_schema, resume_hash, space_digest, rng_schema,
                sealed_run_seed, status, contract_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("scale-run", "v1", "resume", "space", "rng", "seed", "running", "{}", now, now),
        )
        ledger.connection.executemany(
            """
            INSERT INTO updates (
                run_id, update_idx, state, theta_before_json, theta_final_json,
                schedule_json, ltc_required, revision, created_at, updated_at
            ) VALUES (?, ?, 'COMMITTED', ?, ?, '{}', 0, ?, ?, ?)
            """,
            [
                (
                    "scale-run",
                    update_idx,
                    json.dumps({"p": float(update_idx - 1)}),
                    json.dumps({"p": float(update_idx)}),
                    update_idx,
                    now,
                    now,
                )
                for update_idx in range(1, updates + 1)
            ],
        )
        ledger.connection.executemany(
            """
            INSERT INTO event_revisions (run_id, revision, event_type, payload_json, created_at)
            VALUES (?, ?, 'update_committed', ?, ?)
            """,
            [
                ("scale-run", update_idx, json.dumps({"update_idx": update_idx}), now)
                for update_idx in range(1, updates + 1)
            ],
        )
        ledger.connection.commit()


def _measure_projection(updates: int, repeats: int) -> float:
    samples: list[float] = []
    for _ in range(repeats):
        with tempfile.TemporaryDirectory(prefix=f"spsa-scale-{updates}-") as raw_dir:
            run_dir = Path(raw_dir)
            _seed_ledger(run_dir, updates=updates)
            started = time.perf_counter()
            projector = SpsaRunProjector(
                run_dir=run_dir,
                db_path=run_dir / "game.db",
                immutable_db=True,
            )
            samples.append((time.perf_counter() - started) * 1_000.0)
            projector.close()
    return statistics.median(samples)


def _measure_projector_queries(updates: int) -> tuple[float, float]:
    with tempfile.TemporaryDirectory(prefix="spsa-scale-query-") as raw_dir:
        run_dir = Path(raw_dir)
        _seed_ledger(run_dir, updates=updates)
        projector = SpsaRunProjector(
            run_dir=run_dir,
            db_path=run_dir / "game.db",
            immutable_db=True,
        )
        query = SpsaUpdateQueryService(
            store=SpsaStore(run_dir=run_dir),
            db_path=run_dir / "game.db",
            read_only=True,
            ledger_update_loader=projector.load_update,
            ledger_updates_loader=projector.load_updates,
            ledger_events_loader=projector.load_events,
            ledger_game_snapshot_loader=projector.game_snapshot,
            ledger_game_snapshots_loader=projector.game_snapshots,
            ledger_game_entries_loader=projector.game_entries,
            ledger_ltc_results_loader=projector.load_ltc_results,
            ledger_revision_loader=projector.revision_state,
            game_batch_loader=projector.load_game_records,
        )
        detail_samples: list[float] = []
        for _ in range(100):
            started = time.perf_counter()
            assert query.build_update_detail(updates)["update_idx"] == updates
            detail_samples.append((time.perf_counter() - started) * 1_000.0)

        revision_samples: list[float] = []
        with open_spsa_ledger(run_dir) as writer:
            for offset in range(1, 31):
                update_idx = updates + offset
                revision = updates + offset
                now = f"2026-01-01T00:00:{offset:02d}+00:00"
                writer.connection.execute(
                    """
                    INSERT INTO updates (
                        run_id, update_idx, state, theta_before_json, theta_final_json,
                        schedule_json, ltc_required, revision, created_at, updated_at
                    ) VALUES (?, ?, 'COMMITTED', ?, ?, '{}', 0, ?, ?, ?)
                    """,
                    (
                        "scale-run",
                        update_idx,
                        json.dumps({"p": float(update_idx - 1)}),
                        json.dumps({"p": float(update_idx)}),
                        revision,
                        now,
                        now,
                    ),
                )
                writer.connection.execute(
                    """
                    INSERT INTO event_revisions (
                        run_id, revision, event_type, payload_json, created_at
                    ) VALUES (?, ?, 'update_committed', ?, ?)
                    """,
                    ("scale-run", revision, json.dumps({"update_idx": update_idx}), now),
                )
                writer.connection.commit()
                started = time.perf_counter()
                projector.refresh()
                revision_samples.append((time.perf_counter() - started) * 1_000.0)
        projector.close()
    return _percentile(detail_samples, 0.95), _percentile(revision_samples, 0.95)


class _GameQuery:
    def __init__(self, count: int) -> None:
        self.entries = [(f"game-{index:05d}", index) for index in range(count)]

    def collect_game_id_entries(self) -> list[tuple[str, int]]:
        return list(self.entries)

    def get_game_event_snapshots(self, game_ids: list[str]) -> dict[str, JsonObject]:
        return {
            game_id: {
                "game_id": game_id,
                "timestamp": int(game_id.removeprefix("game-")),
                "result": "DRAW",
            }
            for game_id in game_ids
        }


def _game_listing_service(count: int) -> SpsaGameListingService:
    return SpsaGameListingService(
        db_path=Path("__missing_scale_game_db__.sqlite3"),
        update_query_service=_GameQuery(count),  # type: ignore[arg-type]
        read_only=True,
    )


def _measure_game_listing(count: int) -> float:
    service = _game_listing_service(count)
    samples: list[float] = []
    for _ in range(50):
        started = time.perf_counter()
        games, total = service.list_games(offset=0, limit=50, search_query="")
        assert len(games) == 50 and total == count
        samples.append((time.perf_counter() - started) * 1_000.0)
    return _percentile(samples, 0.95)


async def _measure_event_loop_stall(count: int) -> float:
    service = _game_listing_service(count)
    interval = 0.005
    overshoots: list[float] = []
    finished = asyncio.Event()

    async def ticker() -> None:
        expected = asyncio.get_running_loop().time() + interval
        while not finished.is_set():
            await asyncio.sleep(interval)
            now = asyncio.get_running_loop().time()
            overshoots.append(max(0.0, (now - expected) * 1_000.0))
            expected = now + interval

    def sustained_worker_load() -> None:
        for _ in range(100):
            service.list_games(0, 50, "")

    ticker_task = asyncio.create_task(ticker())
    await asyncio.to_thread(sustained_worker_load)
    await asyncio.sleep(interval * 2)
    finished.set()
    await ticker_task
    return max(overshoots, default=0.0)


async def _read_initial_event(response: ClientResponse) -> None:
    await asyncio.wait_for(response.content.readuntil(b"\n\n"), timeout=5.0)


async def _measure_idle_sse_cpu(duration_seconds: float) -> float:
    with tempfile.TemporaryDirectory(prefix="spsa-scale-sse-") as raw_dir:
        run_dir = Path(raw_dir)
        _seed_ledger(run_dir, updates=1)
        db_path = run_dir / "game.db"
        repository = SQLiteShogiDBFactory(db_path).create()
        repository.create_tables()
        repository.close_db()
        (run_dir / "spsa" / "meta.json").write_text('{"type":"spsa"}', encoding="utf-8")
        server = build_default_root().api_server_factory(
            db_path=db_path,
            port=8080,
            run_dir=run_dir,
            instance_pool=None,
            read_only=False,
            dashboard_num_workers=1,
            dashboard_profiles=("spsa",),
        )
        async with TestClient(TestServer(server.app)) as client:
            response = await client.get("/api/spsa/revisions/stream")
            await _read_initial_event(response)
            cpu_started = time.process_time()
            await asyncio.sleep(duration_seconds)
            cpu_ms = (time.process_time() - cpu_started) * 1_000.0
            response.close()
    return cpu_ms


async def _run(repeats: int, idle_seconds: float) -> JsonObject:
    results: dict[str, float] = {
        "projection_1k_ms": _measure_projection(1_000, repeats),
        "projection_10k_ms": _measure_projection(10_000, repeats),
    }
    detail_p95, revision_p95 = _measure_projector_queries(10_000)
    results["single_revision_p95_ms"] = revision_p95
    results["detail_p95_ms"] = detail_p95
    results["game_list_p95_ms"] = _measure_game_listing(10_000)
    results["event_loop_stall_ms"] = await _measure_event_loop_stall(10_000)
    results["idle_sse_cpu_ms"] = await _measure_idle_sse_cpu(idle_seconds)
    failures = {
        metric: {"measured": value, "threshold": THRESHOLDS[metric]}
        for metric, value in results.items()
        if value > THRESHOLDS[metric]
    }
    return {
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "processor": platform.processor(),
            "projection_repeats": repeats,
            "idle_sse_seconds": idle_seconds,
        },
        "fixture": {
            "projection_updates": [1_000, 10_000],
            "incremental_revisions": 30,
            "detail_samples": 100,
            "game_entries": 10_000,
            "game_list_samples": 50,
        },
        "thresholds_ms": THRESHOLDS,
        "results_ms": {name: round(value, 3) for name, value in results.items()},
        "status": "pass" if not failures else "fail",
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--idle-seconds", type=float, default=3.0)
    args = parser.parse_args()
    if args.repeats < 1 or args.idle_seconds < 1.0:
        parser.error("repeats must be positive and idle-seconds must be at least 1")
    result = asyncio.run(_run(args.repeats, args.idle_seconds))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
