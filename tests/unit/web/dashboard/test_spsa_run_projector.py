from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from shogiarena._core.contexts.dashboard.adapters.spsa import run_projector
from shogiarena._core.contexts.dashboard.adapters.spsa.run_projector import (
    SpsaRunProjectionError,
    SpsaRunProjector,
)
from shogiarena._core.contexts.dashboard.adapters.spsa.update_query_service import SpsaUpdateQueryService
from shogiarena._core.contexts.dashboard.application.spsa.data_store import SpsaStore
from shogiarena._core.contexts.spsa.adapters.ledger_store import SpsaLedger, open_spsa_ledger


def _write_ledger(run_dir: Path, *, updates: int) -> None:
    with open_spsa_ledger(run_dir) as ledger:
        ledger.connection.execute(
            """
            INSERT INTO run_contract (
                run_id, contract_schema, resume_hash, space_digest, rng_schema,
                sealed_run_seed, status, contract_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-1",
                "v1",
                "resume",
                "space",
                "rng",
                "seed",
                "running",
                "{}",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        ledger.connection.executemany(
            """
            INSERT INTO updates (
                run_id, update_idx, state, theta_before_json, theta_final_json, schedule_json,
                ltc_required, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "run-1",
                    idx,
                    "COMMITTED" if idx < updates else "PLANNED",
                    json.dumps({"p": float(idx)}),
                    json.dumps({"p": float(idx)}) if idx < updates else None,
                    "{}",
                    0,
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                )
                for idx in range(1, updates + 1)
            ],
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
                '{"games":[{"game_id":"game-1","tuned_as":"black"}]}',
                "{}",
                "{}",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        ledger.connection.execute(
            """
            INSERT INTO game_observations (
                run_id, game_id, update_idx, pair_id, attempt_id, observation_kind,
                result_kind, game_db_id, evidence_digest, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-1",
                "game-1",
                1,
                "pair-1",
                "attempt-1",
                "SPSA",
                "DRAW",
                1,
                "digest",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        ledger.connection.commit()


def test_projector_builds_indices_and_refreshes_only_changed_update(tmp_path: Path) -> None:
    _write_ledger(tmp_path, updates=200)
    # 実行中の run を模す。``immutable_db=True`` はアーカイブ閲覧専用で、後続の
    # commit を観測しない(task 0066)。
    projector = SpsaRunProjector(run_dir=tmp_path, db_path=tmp_path / "game.db", immutable_db=False)

    assert projector.pair_ids(1) == ("pair-1",)
    assert projector.game_identity("game-1") == {
        "game_id": "game-1",
        "update_idx": 1,
        "pair_id": "pair-1",
        "observation_kind": "SPSA",
        "result_kind": "DRAW",
        "game_db_id": 1,
        "observed_at": "2026-01-01T00:00:00+00:00",
    }
    assert projector.load_update(200)["params"] == {"p": 200.0}  # type: ignore[index]

    with open_spsa_ledger(tmp_path) as ledger:
        ledger.connection.execute(
            """
            UPDATE updates
            SET state = 'COMMITTED', theta_final_json = ?, revision = 1, updated_at = ?
            WHERE run_id = ? AND update_idx = ?
            """,
            ('{"p":201.0}', "2026-01-01T00:00:01+00:00", "run-1", 200),
        )
        ledger.connection.execute(
            """
            INSERT INTO event_revisions (run_id, revision, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("run-1", 1, "update_committed", '{"update_idx":200}', "2026-01-01T00:00:01+00:00"),
        )
        ledger.connection.commit()

    updated = projector.load_update(200)
    assert updated is not None
    assert updated["ledger_state"] == "COMMITTED"
    assert updated["params"] == {"p": 201.0}
    assert projector.revision == 1
    assert projector.last_refreshed_updates == (200,)
    projector.close()


def test_projector_invalidates_event_cache_for_terminal_revision(tmp_path: Path) -> None:
    _write_ledger(tmp_path, updates=1)
    projector = SpsaRunProjector(run_dir=tmp_path, db_path=tmp_path / "game.db", immutable_db=False)
    assert all(event["event"] != "terminal" for event in projector.load_events())

    with open_spsa_ledger(tmp_path) as ledger:
        ledger.connection.execute(
            """
            INSERT INTO event_revisions (run_id, revision, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "run-1",
                1,
                "terminal",
                '{"status":"clean","reason":"completed","resumable":false}',
                "2026-01-01T00:00:01+00:00",
            ),
        )
        ledger.connection.commit()

    events = projector.load_events()

    assert any(event["event"] == "terminal" for event in events)
    assert projector.revision == 1
    assert projector.last_refreshed_updates == ()
    projector.close()


def test_archive_projector_does_not_observe_writes_made_after_it_opened(tmp_path: Path) -> None:
    """``immutable_db=True`` はアーカイブ閲覧専用であることを明示する。

    ``immutable=1`` は SQLite に「このファイルは変化しない」と宣言するので、変更検出も
    WAL の回復も行わない。アーカイブツリーに sidecar を作らないための必須条件だが、
    **進行中の run に使うと永久に凍って見える。** `dashboard serve` は実行中に見える run を
    明示エラーで拒否する(archive snapshot resolver)ので、この制約は成立する。
    """

    _write_ledger(tmp_path, updates=1)
    projector = SpsaRunProjector(run_dir=tmp_path, db_path=tmp_path / "game.db", immutable_db=True)
    assert all(event["event"] != "terminal" for event in projector.load_events())

    with open_spsa_ledger(tmp_path) as ledger:
        ledger.connection.execute(
            """
            INSERT INTO event_revisions (run_id, revision, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "run-1",
                1,
                "terminal",
                '{"status":"clean","reason":"completed","resumable":false}',
                "2026-01-01T00:00:01+00:00",
            ),
        )
        ledger.connection.commit()

    assert all(event["event"] != "terminal" for event in projector.load_events())
    projector.close()


def test_projector_batch_hydrates_games_with_one_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_ledger(tmp_path, updates=1)
    execute_calls = 0

    class _Result:
        def all(self) -> list[tuple[object, ...]]:
            now = datetime(2026, 1, 1, tzinfo=UTC)
            return [
                ("g1", "A", "B", "DRAW", 10, now, now),
                ("g2", "C", "D", "BLACK_WIN", 20, now, now),
            ]

    class _Session:
        def execute(self, _statement: object) -> _Result:
            nonlocal execute_calls
            execute_calls += 1
            return _Result()

    class _Repository:
        session = _Session()
        operation_boundaries = 0

        @contextmanager
        def operation(self, *, commit: bool = False) -> Iterator[_Session]:
            del commit
            type(self).operation_boundaries += 1
            yield self.session

        def close_db(self) -> None:
            return

    monkeypatch.setattr(run_projector, "open_dashboard_repository", lambda *_args, **_kwargs: _Repository())
    projector = SpsaRunProjector(run_dir=tmp_path, db_path=tmp_path / "game.db", immutable_db=True)

    records = projector.load_game_records(["g1", "g2"])
    # Each call must open and close a session boundary so later reads are not served
    # from a stale read transaction held open by the reused engine.
    assert _Repository.operation_boundaries == 1

    assert execute_calls == 1
    assert set(records) == {"g1", "g2"}
    projector.close()
    with pytest.raises(RuntimeError, match="closed"):
        projector.refresh()


def test_projector_startup_is_batched_and_unchanged_refresh_is_constant_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_ledger(tmp_path, updates=200)
    statements: list[str] = []
    real_open = run_projector.open_spsa_ledger

    def _open_traced(run_dir: Path, *, read_only: bool = False, immutable: bool = False) -> SpsaLedger:
        ledger = real_open(run_dir, read_only=read_only, immutable=immutable)
        ledger.connection.set_trace_callback(statements.append)
        return ledger

    monkeypatch.setattr(run_projector, "open_spsa_ledger", _open_traced)
    projector = SpsaRunProjector(run_dir=tmp_path, db_path=tmp_path / "game.db", immutable_db=True)

    history_queries = [
        statement
        for statement in statements
        if any(table in statement for table in ("FROM updates", "FROM pair_assignments", "FROM game_observations"))
    ]
    assert len(history_queries) == 3

    statements.clear()
    assert projector.load_update(200) is not None
    assert len(projector.load_updates()) == 199
    assert projector.game_snapshot("game-1") is not None
    projector.summary_aggregates()

    assert not any(
        table in statement
        for statement in statements
        for table in ("FROM updates", "FROM pair_assignments", "FROM game_observations")
    )
    projector.close()


def test_projector_batch_game_snapshots_use_one_ledger_poll(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_ledger(tmp_path, updates=1)
    statements: list[str] = []
    real_open = run_projector.open_spsa_ledger

    def _open_traced(run_dir: Path, *, read_only: bool = False, immutable: bool = False) -> SpsaLedger:
        ledger = real_open(run_dir, read_only=read_only, immutable=immutable)
        ledger.connection.set_trace_callback(statements.append)
        return ledger

    monkeypatch.setattr(run_projector, "open_spsa_ledger", _open_traced)
    projector = SpsaRunProjector(run_dir=tmp_path, db_path=tmp_path / "game.db", immutable_db=True)

    statements.clear()
    snapshots = projector.game_snapshots(["game-1"] * 100)

    assert set(snapshots) == {"game-1"}
    assert sum(statement.startswith("PRAGMA data_version") for statement in statements) == 1
    projector.close()


def test_ledger_detail_does_not_materialize_full_event_history(tmp_path: Path) -> None:
    _write_ledger(tmp_path, updates=200)
    projector = SpsaRunProjector(run_dir=tmp_path, db_path=tmp_path / "game.db", immutable_db=True)

    def _unexpected_history_load() -> list[dict[str, object]]:
        raise AssertionError("ledger-backed detail must not load full event history")

    query = SpsaUpdateQueryService(
        store=SpsaStore(run_dir=tmp_path),
        db_path=tmp_path / "game.db",
        read_only=True,
        ledger_update_loader=projector.load_update,
        ledger_events_loader=_unexpected_history_load,
        ledger_game_snapshot_loader=projector.game_snapshot,
        ledger_game_snapshots_loader=projector.game_snapshots,
        game_batch_loader=projector.load_game_records,
    )

    detail = query.build_update_detail(1)

    assert detail["update_idx"] == 1
    assert detail["wdl"] == {"wins": 0, "losses": 0, "draws": 1}
    projector.close()


def test_projector_exposes_authoritative_operational_status(tmp_path: Path) -> None:
    _write_ledger(tmp_path, updates=2)
    projector = SpsaRunProjector(run_dir=tmp_path, db_path=tmp_path / "game.db", immutable_db=True)

    status = projector.operational_status()

    assert status["run_id"] == "run-1"
    assert status["contract"] == {
        "schema_version": "v1",
        "resume_hash": "resume",
        "space_digest": "space",
    }
    assert status["completion"] == {
        "status": "running",
        "termination_reason": None,
        "last_committed_update": 1,
        "pending_update": 2,
        "pending_stage": "PLANNED",
        "resumable": None,
    }
    assert status["ledger"]["revision"] == 0  # type: ignore[index]
    assert status["remote_execution"] == {"status": "not_observed"}
    assert status["node_multiplier"] == {"status": "not_applicable", "value": None}
    projector.close()


def test_projector_exposes_latest_remote_participation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_ledger(tmp_path, updates=1)

    class _Result:
        def first(self) -> tuple[object, ...]:
            return (
                "worker-a",
                {
                    "remote_execution": {
                        "endpoint_identity": "endpoint-a",
                        "deployment_id": "deploy-1",
                        "job_id": "job-1",
                        "attempt_id": "attempt-1",
                        "spsa_run_id": "run-1",
                        "spsa_update_idx": 1,
                        "spsa_pair_id": "pair-1",
                        "effective_node_multiplier": 2,
                    }
                },
                {"sha256": "engine-digest"},
            )

    class _Session:
        def execute(self, _statement: object) -> _Result:
            return _Result()

    class _Repository:
        session = _Session()
        open_calls = 0

        @contextmanager
        def operation(self, *, commit: bool = False) -> Iterator[_Session]:
            del commit
            yield self.session

        def close_db(self) -> None:
            return

    def _open_repository(*_args: object, **_kwargs: object) -> _Repository:
        _Repository.open_calls += 1
        return _Repository()

    monkeypatch.setattr(run_projector, "open_dashboard_repository", _open_repository)
    projector = SpsaRunProjector(run_dir=tmp_path, db_path=tmp_path / "game.db", immutable_db=True)

    status = projector.operational_status()
    projector.operational_status()
    # Rebuilding the SQLAlchemy engine dominates this call, so the repository is opened once.
    assert _Repository.open_calls == 1

    assert status["remote_execution"] == {
        "status": "observed",
        "instance_id": "worker-a",
        "endpoint_identity": "endpoint-a",
        "deployment_id": "deploy-1",
        "job_id": "job-1",
        "participation": {
            "run_id": "run-1",
            "update_idx": 1,
            "pair_id": "pair-1",
            "attempt_id": "attempt-1",
        },
        "engine_digest": "engine-digest",
    }
    assert status["node_multiplier"] == {"status": "applied", "value": 2}
    projector.close()


@pytest.mark.parametrize(
    ("assignment", "message"),
    [
        ("theta_final_json = '{', state = 'COMMITTED'", "final theta"),
        ("theta_final_json = NULL, state = 'COMMITTED'", "no final theta"),
        ("schedule_json = '{'", "schedule"),
    ],
)
def test_projector_rejects_corrupt_authoritative_update_json(
    tmp_path: Path,
    assignment: str,
    message: str,
) -> None:
    _write_ledger(tmp_path, updates=2)
    with open_spsa_ledger(tmp_path) as ledger:
        ledger.connection.execute(f"UPDATE updates SET {assignment} WHERE run_id = 'run-1' AND update_idx = 1")
        ledger.connection.commit()

    with pytest.raises(SpsaRunProjectionError, match=message):
        SpsaRunProjector(run_dir=tmp_path, db_path=tmp_path / "game.db", immutable_db=True)


def test_update_entries_carry_the_time_span_the_dashboard_renders(tmp_path: Path) -> None:
    """Updates テーブルの Start / Finish 列が空にならないことを表明する。

    フロントは update エントリの `started_at` / `ended_at` を読む。ledger の
    `updates` 行は commit 時刻しか持たないので、開始は pair 割り当ての
    `created_at`、終了は対局観測の最終時刻から導く必要がある。
    ここが欠けていると、実行は正常でも表の 2 列が恒久的に "-" になる。
    """

    _write_ledger(tmp_path, updates=2)
    projector = SpsaRunProjector(run_dir=tmp_path, db_path=tmp_path / "game.db", immutable_db=True)
    try:
        entry = next(item for item in projector.load_updates() if item["update_idx"] == 1)
    finally:
        projector.close()

    expected = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
    assert entry["started_at"] == expected
    assert entry["ended_at"] == expected


def test_update_without_finished_games_reports_no_end_time(tmp_path: Path) -> None:
    """まだ 1 局も終わっていない update では終了時刻を捏造しない。"""

    _write_ledger(tmp_path, updates=2)
    with open_spsa_ledger(tmp_path) as ledger:
        ledger.connection.execute("DELETE FROM game_observations")
        ledger.connection.commit()

    projector = SpsaRunProjector(run_dir=tmp_path, db_path=tmp_path / "game.db", immutable_db=True)
    try:
        entry = next(item for item in projector.load_updates() if item["update_idx"] == 1)
    finally:
        projector.close()

    assert entry["started_at"] == int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
    assert entry["ended_at"] is None


def test_update_wdl_counts_tuning_games_only(tmp_path: Path) -> None:
    """W-D-L は θ+ 対 θ− の tuning 対局だけを数える。

    LTC 対局の "tuned" は候補 vs 承認済みベースラインという別の比較なので、
    混ぜると 1 つの数字が 2 種類の比較を指すことになる。LTC は専用の列で出す。
    """

    _write_ledger(tmp_path, updates=2)
    with open_spsa_ledger(tmp_path) as ledger:
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
                "pair-ltc",
                "LTC",
                "v1",
                "digest",
                "{}",
                '{"games":[{"game_id":"game-ltc","tuned_as":"black"}]}',
                "{}",
                "{}",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        ledger.connection.execute(
            """
            INSERT INTO game_observations (
                run_id, game_id, update_idx, pair_id, attempt_id, observation_kind,
                result_kind, game_db_id, evidence_digest, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-1",
                "game-ltc",
                1,
                "pair-ltc",
                "attempt-ltc",
                "LTC",
                "BLACK_WIN",
                2,
                "digest",
                "2026-01-02T00:00:00+00:00",
            ),
        )
        ledger.connection.commit()

    projector = SpsaRunProjector(run_dir=tmp_path, db_path=tmp_path / "game.db", immutable_db=True)
    try:
        entry = next(item for item in projector.load_updates() if item["update_idx"] == 1)
    finally:
        projector.close()

    # tuning 対局は 1 局（DRAW）だけ。LTC の BLACK_WIN を混ぜてはいけない。
    assert (entry["wins"], entry["draws"], entry["losses"]) == (0, 1, 0)
    assert entry["ltc_game_ids"] == ["game-ltc"]
    # Finish も tuning 対局の最終時刻であって、LTC の完了時刻ではない。
    assert entry["ended_at"] == int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
