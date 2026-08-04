from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from shogiarena._core.contexts.spsa.adapters.ledger_schema import (
    APPLICATION_SCHEMA_ID,
    CURRENT_SPSA_LEDGER_SCHEMA_VERSION,
    SpsaLedgerSchemaError,
    canonical_table_names,
    create_canonical_schema,
    schema_digest,
)
from shogiarena._core.contexts.spsa.adapters.ledger_store import (
    open_spsa_ledger,
    recover_spsa_ledger,
)
from shogiarena._core.contexts.spsa.ports.ledger_ports import SPSA_LEDGER_RELATIVE_PATH

_HOT_JOURNAL_WORKER = Path(__file__).parents[3] / "helpers" / "spsa_hot_journal_worker.py"
_CRASH_EXIT = 87


def _user_version(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        row = connection.execute("PRAGMA user_version").fetchone()
        assert row is not None
        return int(row[0])


def test_new_ledger_creates_and_versions_the_canonical_schema(tmp_path: Path) -> None:
    with open_spsa_ledger(tmp_path) as ledger:
        tables = {
            str(row[0])
            for row in ledger.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        metadata = ledger.connection.execute(
            "SELECT application_schema_id, schema_digest FROM ledger_metadata WHERE singleton_id = 1"
        ).fetchone()

    assert tables == set(canonical_table_names())
    assert metadata == (APPLICATION_SCHEMA_ID, schema_digest())
    assert _user_version(tmp_path / SPSA_LEDGER_RELATIVE_PATH) == CURRENT_SPSA_LEDGER_SCHEMA_VERSION


def test_read_only_open_validates_without_mutating_ledger(tmp_path: Path) -> None:
    with open_spsa_ledger(tmp_path):
        pass
    path = tmp_path / SPSA_LEDGER_RELATIVE_PATH
    before = path.read_bytes()

    with open_spsa_ledger(tmp_path, read_only=True) as ledger:
        assert ledger.is_read_only
        assert ledger.connection.execute("SELECT COUNT(*) FROM run_contract").fetchone() == (0,)
        with pytest.raises(sqlite3.OperationalError):
            ledger.connection.execute("INSERT INTO run_contract(run_id) VALUES ('forbidden')")

    assert path.read_bytes() == before


def test_connections_use_bounded_busy_timeout(tmp_path: Path) -> None:
    with open_spsa_ledger(tmp_path) as ledger:
        assert ledger.connection.execute("PRAGMA busy_timeout").fetchone() == (30_000,)
    # dashboard と派生 JSON 投影は次のポーリングまで待てるので、reader 側は短く打ち切る。
    with open_spsa_ledger(tmp_path, read_only=True) as ledger:
        assert ledger.connection.execute("PRAGMA busy_timeout").fetchone() == (2_000,)


def test_recovery_does_not_mutate_a_healthy_current_ledger(tmp_path: Path) -> None:
    with open_spsa_ledger(tmp_path):
        pass
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    recover_spsa_ledger(tmp_path)

    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


def test_writable_recovery_rolls_back_hot_journal_before_read_only_validation(tmp_path: Path) -> None:
    with open_spsa_ledger(tmp_path) as ledger:
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
        ledger.connection.commit()
    ledger_path = tmp_path / SPSA_LEDGER_RELATIVE_PATH
    crashed = subprocess.run(
        [sys.executable, str(_HOT_JOURNAL_WORKER), str(ledger_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert crashed.returncode == _CRASH_EXIT, crashed.stderr
    assert ledger_path.with_name(f"{ledger_path.name}-journal").is_file()

    with pytest.raises(SpsaLedgerSchemaError, match="SQLITE_READONLY_ROLLBACK"):
        open_spsa_ledger(tmp_path, read_only=True)

    recover_spsa_ledger(tmp_path)

    with open_spsa_ledger(tmp_path, read_only=True) as ledger:
        assert ledger.connection.execute("SELECT COUNT(*) FROM event_revisions").fetchone() == (0,)


@pytest.mark.parametrize("read_only", [False, True])
def test_unknown_newer_schema_version_is_rejected(tmp_path: Path, read_only: bool) -> None:
    with open_spsa_ledger(tmp_path):
        pass
    path = tmp_path / SPSA_LEDGER_RELATIVE_PATH
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version={CURRENT_SPSA_LEDGER_SCHEMA_VERSION + 1}")

    with pytest.raises(
        SpsaLedgerSchemaError,
        match=f"schema version {CURRENT_SPSA_LEDGER_SCHEMA_VERSION + 1}",
    ):
        open_spsa_ledger(tmp_path, read_only=read_only)


def test_partial_unversioned_schema_is_rejected_without_stamping(tmp_path: Path) -> None:
    path = tmp_path / SPSA_LEDGER_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE run_contract(run_id TEXT PRIMARY KEY)")

    before = path.read_bytes()
    with pytest.raises(SpsaLedgerSchemaError, match="missing tables"):
        open_spsa_ledger(tmp_path)

    assert _user_version(path) == 0
    assert path.read_bytes() == before


def test_versioned_empty_database_is_not_silently_repaired(tmp_path: Path) -> None:
    path = tmp_path / SPSA_LEDGER_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version={CURRENT_SPSA_LEDGER_SCHEMA_VERSION}")

    before = path.read_bytes()
    with pytest.raises(SpsaLedgerSchemaError, match="no canonical schema"):
        open_spsa_ledger(tmp_path)

    assert path.read_bytes() == before


def test_compatible_unversioned_schema_is_stamped_only_by_writer(tmp_path: Path) -> None:
    path = tmp_path / SPSA_LEDGER_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        create_canonical_schema(connection)
    assert _user_version(path) == 0

    with open_spsa_ledger(tmp_path, read_only=True):
        pass
    assert _user_version(path) == 0

    with open_spsa_ledger(tmp_path):
        pass
    assert _user_version(path) == CURRENT_SPSA_LEDGER_SCHEMA_VERSION


def test_missing_required_index_is_rejected(tmp_path: Path) -> None:
    with open_spsa_ledger(tmp_path):
        pass
    path = tmp_path / SPSA_LEDGER_RELATIVE_PATH
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX game_observations_pair_idx")

    with pytest.raises(SpsaLedgerSchemaError, match="named index set"):
        open_spsa_ledger(tmp_path)


def test_corrupt_ledger_has_actionable_error(tmp_path: Path) -> None:
    path = tmp_path / SPSA_LEDGER_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not a sqlite database")

    with pytest.raises(SpsaLedgerSchemaError, match="Restore|fresh run"):
        open_spsa_ledger(tmp_path)


def test_identity_state_and_revision_constraints_fail_closed(tmp_path: Path) -> None:
    with open_spsa_ledger(tmp_path) as ledger:
        connection = ledger.connection
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO run_contract(
                    run_id, contract_schema, resume_hash, space_digest, rng_schema,
                    sealed_run_seed, status, contract_json, created_at, updated_at, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("run", "v1", "r", "s", "rng", "seed", "invalid", "{}", "now", "now", 0),
            )
        connection.execute(
            """
            INSERT INTO run_contract(
                run_id, contract_schema, resume_hash, space_digest, rng_schema,
                sealed_run_seed, status, contract_json, created_at, updated_at, revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("run", "v1", "r", "s", "rng", "seed", "running", "{}", "now", "now", 0),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO updates(
                    run_id, update_idx, state, theta_before_json, ltc_required,
                    revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("run", 1, "INVALID", "{}", 0, 0, "now", "now"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO event_revisions(run_id, revision, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("run", 0, "invalid", "{}", "now"),
            )
