"""SPSA optimizer ledger の canonical SQLite schema と版検証。"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable

CURRENT_SPSA_LEDGER_SCHEMA_VERSION = 3
APPLICATION_SCHEMA_ID = "shogiarena.spsa-ledger.v1"

_REPAIR_GUIDANCE = (
    "Restore the SPSA ledger from backup, or start a fresh run with --no-resume. "
    "ShogiArena does not rewrite incompatible SPSA ledgers."
)

_TABLE_STATEMENTS = (
    """
    CREATE TABLE ledger_metadata (
        singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
        application_schema_id TEXT NOT NULL,
        schema_digest TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE run_contract (
        run_id TEXT PRIMARY KEY,
        contract_schema TEXT NOT NULL,
        resume_hash TEXT NOT NULL,
        space_digest TEXT NOT NULL,
        rng_schema TEXT NOT NULL,
        sealed_run_seed TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('initializing', 'running', 'terminal')),
        contract_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0)
    )
    """,
    """
    CREATE TABLE parameters (
        run_id TEXT NOT NULL,
        parameter_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        option_name TEXT NOT NULL,
        parameter_type TEXT NOT NULL CHECK (parameter_type IN ('int', 'float')),
        minimum_value TEXT NOT NULL,
        maximum_value TEXT NOT NULL,
        initial_value TEXT NOT NULL,
        rounding_policy TEXT NOT NULL,
        schedule_json TEXT NOT NULL,
        PRIMARY KEY (run_id, parameter_id),
        UNIQUE (run_id, ordinal),
        UNIQUE (run_id, option_name),
        FOREIGN KEY (run_id) REFERENCES run_contract(run_id) ON UPDATE RESTRICT ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE updates (
        run_id TEXT NOT NULL,
        update_idx INTEGER NOT NULL CHECK (update_idx >= 1),
        state TEXT NOT NULL CHECK (
            state IN (
                'PLANNED', 'GAMES_RUNNING', 'GAMES_COMPLETE', 'CANDIDATE_COMPUTED',
                'LTC_PENDING', 'LTC_RUNNING', 'ACCEPTED', 'REVERTED', 'COMMITTED'
            )
        ),
        theta_before_json TEXT NOT NULL,
        theta_candidate_json TEXT,
        theta_final_json TEXT,
        schedule_json TEXT,
        ltc_required INTEGER NOT NULL CHECK (ltc_required IN (0, 1)),
        revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (run_id, update_idx),
        FOREIGN KEY (run_id) REFERENCES run_contract(run_id) ON UPDATE RESTRICT ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE pair_assignments (
        run_id TEXT NOT NULL,
        update_idx INTEGER NOT NULL,
        pair_id TEXT NOT NULL,
        assignment_kind TEXT NOT NULL CHECK (assignment_kind IN ('SPSA', 'LTC')),
        assignment_schema TEXT NOT NULL,
        assignment_digest TEXT NOT NULL,
        opening_json TEXT NOT NULL,
        color_assignment_json TEXT NOT NULL,
        flip_json TEXT NOT NULL,
        rounding_samples_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, pair_id),
        UNIQUE (run_id, update_idx, pair_id),
        FOREIGN KEY (run_id, update_idx)
            REFERENCES updates(run_id, update_idx) ON UPDATE RESTRICT ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE game_observations (
        run_id TEXT NOT NULL,
        game_id TEXT NOT NULL,
        update_idx INTEGER NOT NULL,
        pair_id TEXT NOT NULL,
        attempt_id TEXT NOT NULL,
        observation_kind TEXT NOT NULL CHECK (observation_kind IN ('SPSA', 'LTC')),
        result_kind TEXT NOT NULL CHECK (
            result_kind IN (
                'BLACK_WIN', 'WHITE_WIN', 'DRAW', 'INCOMPLETE', 'FAILED_OBSERVATION'
            )
        ),
        game_db_id INTEGER NOT NULL CHECK (game_db_id >= 1),
        evidence_digest TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        PRIMARY KEY (run_id, game_id),
        UNIQUE (run_id, pair_id, attempt_id),
        FOREIGN KEY (run_id, update_idx, pair_id)
            REFERENCES pair_assignments(run_id, update_idx, pair_id) ON UPDATE RESTRICT ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE ltc_decisions (
        run_id TEXT NOT NULL,
        tested_update_idx INTEGER NOT NULL,
        baseline_update_idx INTEGER,
        decision TEXT NOT NULL CHECK (decision IN ('pass', 'fail')),
        evidence_digest TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        final_theta_json TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        PRIMARY KEY (run_id, tested_update_idx),
        FOREIGN KEY (run_id, tested_update_idx)
            REFERENCES updates(run_id, update_idx) ON UPDATE RESTRICT ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE accepted_baseline (
        run_id TEXT PRIMARY KEY,
        accepted_update_idx INTEGER NOT NULL CHECK (accepted_update_idx >= 0),
        theta_json TEXT NOT NULL,
        evidence_digest TEXT,
        accepted_at TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 0),
        FOREIGN KEY (run_id) REFERENCES run_contract(run_id) ON UPDATE RESTRICT ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE terminal_state (
        run_id TEXT PRIMARY KEY,
        status TEXT NOT NULL CHECK (status IN ('clean', 'with-anomalies', 'failed')),
        reason TEXT NOT NULL CHECK (
            reason IN ('completed', 'early_stopped', 'cancelled_resumable', 'failed')
        ),
        last_committed_update INTEGER NOT NULL CHECK (last_committed_update >= 0),
        pending_stage TEXT,
        resumable INTEGER NOT NULL CHECK (resumable IN (0, 1)),
        committed_at TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        FOREIGN KEY (run_id) REFERENCES run_contract(run_id) ON UPDATE RESTRICT ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE event_revisions (
        run_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, revision),
        FOREIGN KEY (run_id) REFERENCES run_contract(run_id) ON UPDATE RESTRICT ON DELETE RESTRICT
    )
    """,
)

_INDEX_STATEMENTS = (
    "CREATE INDEX updates_state_idx ON updates(run_id, state, update_idx)",
    "CREATE INDEX pair_assignments_update_idx ON pair_assignments(run_id, update_idx)",
    "CREATE INDEX game_observations_pair_idx ON game_observations(run_id, pair_id)",
    "CREATE INDEX game_observations_db_idx ON game_observations(game_db_id)",
    "CREATE INDEX event_revisions_type_idx ON event_revisions(run_id, event_type, revision)",
)

_EXPECTED_TABLE_SQL: dict[str, str] = {
    statement.strip().split(maxsplit=3)[2]: " ".join(statement.strip().replace("\n", " ").split())
    for statement in _TABLE_STATEMENTS
}

_EXPECTED_COLUMNS: dict[str, tuple[tuple[str, str, int, int], ...]] = {
    "ledger_metadata": (
        ("singleton_id", "INTEGER", 0, 1),
        ("application_schema_id", "TEXT", 1, 0),
        ("schema_digest", "TEXT", 1, 0),
    ),
    "run_contract": (
        ("run_id", "TEXT", 0, 1),
        ("contract_schema", "TEXT", 1, 0),
        ("resume_hash", "TEXT", 1, 0),
        ("space_digest", "TEXT", 1, 0),
        ("rng_schema", "TEXT", 1, 0),
        ("sealed_run_seed", "TEXT", 1, 0),
        ("status", "TEXT", 1, 0),
        ("contract_json", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
        ("revision", "INTEGER", 1, 0),
    ),
    "parameters": (
        ("run_id", "TEXT", 1, 1),
        ("parameter_id", "TEXT", 1, 2),
        ("ordinal", "INTEGER", 1, 0),
        ("option_name", "TEXT", 1, 0),
        ("parameter_type", "TEXT", 1, 0),
        ("minimum_value", "TEXT", 1, 0),
        ("maximum_value", "TEXT", 1, 0),
        ("initial_value", "TEXT", 1, 0),
        ("rounding_policy", "TEXT", 1, 0),
        ("schedule_json", "TEXT", 1, 0),
    ),
    "updates": (
        ("run_id", "TEXT", 1, 1),
        ("update_idx", "INTEGER", 1, 2),
        ("state", "TEXT", 1, 0),
        ("theta_before_json", "TEXT", 1, 0),
        ("theta_candidate_json", "TEXT", 0, 0),
        ("theta_final_json", "TEXT", 0, 0),
        ("schedule_json", "TEXT", 0, 0),
        ("ltc_required", "INTEGER", 1, 0),
        ("revision", "INTEGER", 1, 0),
        ("created_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "pair_assignments": (
        ("run_id", "TEXT", 1, 1),
        ("update_idx", "INTEGER", 1, 0),
        ("pair_id", "TEXT", 1, 2),
        ("assignment_kind", "TEXT", 1, 0),
        ("assignment_schema", "TEXT", 1, 0),
        ("assignment_digest", "TEXT", 1, 0),
        ("opening_json", "TEXT", 1, 0),
        ("color_assignment_json", "TEXT", 1, 0),
        ("flip_json", "TEXT", 1, 0),
        ("rounding_samples_json", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
    ),
    "game_observations": (
        ("run_id", "TEXT", 1, 1),
        ("game_id", "TEXT", 1, 2),
        ("update_idx", "INTEGER", 1, 0),
        ("pair_id", "TEXT", 1, 0),
        ("attempt_id", "TEXT", 1, 0),
        ("observation_kind", "TEXT", 1, 0),
        ("result_kind", "TEXT", 1, 0),
        ("game_db_id", "INTEGER", 1, 0),
        ("evidence_digest", "TEXT", 1, 0),
        ("observed_at", "TEXT", 1, 0),
    ),
    "ltc_decisions": (
        ("run_id", "TEXT", 1, 1),
        ("tested_update_idx", "INTEGER", 1, 2),
        ("baseline_update_idx", "INTEGER", 0, 0),
        ("decision", "TEXT", 1, 0),
        ("evidence_digest", "TEXT", 1, 0),
        ("evidence_json", "TEXT", 1, 0),
        ("final_theta_json", "TEXT", 1, 0),
        ("decided_at", "TEXT", 1, 0),
        ("revision", "INTEGER", 1, 0),
    ),
    "accepted_baseline": (
        ("run_id", "TEXT", 0, 1),
        ("accepted_update_idx", "INTEGER", 1, 0),
        ("theta_json", "TEXT", 1, 0),
        ("evidence_digest", "TEXT", 0, 0),
        ("accepted_at", "TEXT", 1, 0),
        ("revision", "INTEGER", 1, 0),
    ),
    "terminal_state": (
        ("run_id", "TEXT", 0, 1),
        ("status", "TEXT", 1, 0),
        ("reason", "TEXT", 1, 0),
        ("last_committed_update", "INTEGER", 1, 0),
        ("pending_stage", "TEXT", 0, 0),
        ("resumable", "INTEGER", 1, 0),
        ("committed_at", "TEXT", 1, 0),
        ("revision", "INTEGER", 1, 0),
    ),
    "event_revisions": (
        ("run_id", "TEXT", 1, 1),
        ("revision", "INTEGER", 1, 2),
        ("event_type", "TEXT", 1, 0),
        ("payload_json", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
    ),
}

_REQUIRED_UNIQUES: dict[str, tuple[tuple[str, ...], ...]] = {
    "parameters": (("run_id", "ordinal"), ("run_id", "option_name")),
    "pair_assignments": (("run_id", "update_idx", "pair_id"),),
    "game_observations": (("run_id", "pair_id", "attempt_id"),),
}

_REQUIRED_INDEXES = {
    "updates_state_idx": ("updates", ("run_id", "state", "update_idx")),
    "pair_assignments_update_idx": ("pair_assignments", ("run_id", "update_idx")),
    "game_observations_pair_idx": ("game_observations", ("run_id", "pair_id")),
    "game_observations_db_idx": ("game_observations", ("game_db_id",)),
    "event_revisions_type_idx": ("event_revisions", ("run_id", "event_type", "revision")),
}


class SpsaLedgerSchemaError(RuntimeError):
    """SPSA ledgerのschema/版契約違反。"""


def schema_digest() -> str:
    """Return the canonical schema digest sealed in ledger_metadata."""

    canonical = "\n".join(_normalize_sql(statement) for statement in (*_TABLE_STATEMENTS, *_INDEX_STATEMENTS))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_canonical_schema(connection: sqlite3.Connection) -> None:
    """Create a new canonical schema without stamping user_version."""

    script = ["BEGIN IMMEDIATE;"]
    script.extend(f"{statement.strip()};" for statement in _TABLE_STATEMENTS)
    script.extend(f"{statement};" for statement in _INDEX_STATEMENTS)
    script.append(
        "INSERT INTO ledger_metadata(singleton_id, application_schema_id, schema_digest) "
        f"VALUES (1, '{APPLICATION_SCHEMA_ID}', '{schema_digest()}');"
    )
    script.append("COMMIT;")
    try:
        connection.executescript("\n".join(script))
    except sqlite3.DatabaseError as exc:
        raise _schema_error(f"canonical schema could not be created ({_sqlite_error_label(exc)})") from exc


def validate_canonical_schema(connection: sqlite3.Connection) -> None:
    """Strictly validate tables, columns, constraints, indexes, and metadata."""

    try:
        actual_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        expected_tables = set(_EXPECTED_COLUMNS)
        if actual_tables != expected_tables:
            missing = sorted(expected_tables - actual_tables)
            unexpected = sorted(actual_tables - expected_tables)
            details: list[str] = []
            if missing:
                details.append(f"missing tables: {', '.join(missing)}")
            if unexpected:
                details.append(f"unexpected tables: {', '.join(unexpected)}")
            raise _schema_error("; ".join(details))

        for table, expected_columns in _EXPECTED_COLUMNS.items():
            actual_columns = tuple(
                (str(row[1]), str(row[2]).upper(), int(row[3]), int(row[5]))
                for row in connection.execute(f"PRAGMA table_info({table})")
            )
            if actual_columns != expected_columns:
                raise _schema_error(f"table '{table}' column contract differs from canonical schema")
            _validate_table_sql(connection, table)
            _validate_unique_constraints(connection, table)

        _validate_indexes(connection)
        _validate_foreign_keys(connection)
        metadata = connection.execute(
            "SELECT application_schema_id, schema_digest FROM ledger_metadata WHERE singleton_id = 1"
        ).fetchone()
        if metadata != (APPLICATION_SCHEMA_ID, schema_digest()):
            raise _schema_error("ledger_metadata does not match the canonical application schema")
    except SpsaLedgerSchemaError:
        raise
    except sqlite3.DatabaseError as exc:
        raise _schema_error(f"schema could not be inspected ({_sqlite_error_label(exc)})") from exc


def read_user_version(connection: sqlite3.Connection) -> int:
    """Read PRAGMA user_version with typed corruption errors."""

    try:
        row = connection.execute("PRAGMA user_version").fetchone()
        if row is None:
            raise _schema_error("PRAGMA user_version returned no row")
        return int(row[0])
    except SpsaLedgerSchemaError:
        raise
    except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
        raise _schema_error(f"schema version could not be read ({_sqlite_error_label(exc)})") from exc


def stamp_user_version(connection: sqlite3.Connection) -> None:
    """Stamp the current schema version after strict validation."""

    try:
        connection.execute(f"PRAGMA user_version={CURRENT_SPSA_LEDGER_SCHEMA_VERSION}")
        connection.commit()
    except sqlite3.DatabaseError as exc:
        raise _schema_error(f"schema version could not be stamped ({_sqlite_error_label(exc)})") from exc


def has_managed_tables(connection: sqlite3.Connection) -> bool:
    """Return whether the file already contains application tables."""

    try:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone()
            is not None
        )
    except sqlite3.DatabaseError as exc:
        raise _schema_error(f"database catalog could not be read ({_sqlite_error_label(exc)})") from exc


def _validate_table_sql(connection: sqlite3.Connection, table: str) -> None:
    sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    sql = str(sql_row[0]) if sql_row is not None and sql_row[0] is not None else ""
    normalized = _normalize_sql(sql)
    if normalized != _EXPECTED_TABLE_SQL[table]:
        raise _schema_error(f"table '{table}' SQL constraint contract differs from canonical schema")


def _validate_unique_constraints(connection: sqlite3.Connection, table: str) -> None:
    actual: set[tuple[str, ...]] = set()
    for row in connection.execute(f"PRAGMA index_list({table})"):
        if int(row[2]) != 1:
            continue
        columns = tuple(str(item[2]) for item in connection.execute(f"PRAGMA index_info({row[1]})"))
        actual.add(columns)
    for expected in _REQUIRED_UNIQUES.get(table, ()):
        if expected not in actual:
            raise _schema_error(f"table '{table}' is missing unique constraint on {expected}")


def _validate_indexes(connection: sqlite3.Connection) -> None:
    actual_indexes = {
        str(row[0]): str(row[1])
        for row in connection.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if set(actual_indexes) != set(_REQUIRED_INDEXES):
        raise _schema_error("named index set differs from canonical schema")
    for name, (table, columns) in _REQUIRED_INDEXES.items():
        if actual_indexes[name] != table:
            raise _schema_error(f"index '{name}' targets an unexpected table")
        actual_columns = tuple(str(row[2]) for row in connection.execute(f"PRAGMA index_info({name})"))
        if actual_columns != columns:
            raise _schema_error(f"index '{name}' column contract differs from canonical schema")


def _validate_foreign_keys(connection: sqlite3.Connection) -> None:
    expected_counts = {
        "ledger_metadata": 0,
        "run_contract": 0,
        "parameters": 1,
        "updates": 1,
        "pair_assignments": 2,
        "game_observations": 3,
        "ltc_decisions": 2,
        "accepted_baseline": 1,
        "terminal_state": 1,
        "event_revisions": 1,
    }
    for table, expected_count in expected_counts.items():
        rows = tuple(connection.execute(f"PRAGMA foreign_key_list({table})"))
        if len(rows) != expected_count:
            raise _schema_error(f"table '{table}' foreign-key contract differs from canonical schema")
        for row in rows:
            if str(row[5]).upper() != "RESTRICT" or str(row[6]).upper() != "RESTRICT":
                raise _schema_error(f"table '{table}' foreign-key actions differ from canonical schema")


def _normalize_sql(value: str) -> str:
    return " ".join(value.replace("\n", " ").split())


def _sqlite_error_label(exc: BaseException) -> str:
    error_name = getattr(exc, "sqlite_errorname", None)
    if isinstance(error_name, str):
        return error_name
    return str(exc)


def _schema_error(detail: str) -> SpsaLedgerSchemaError:
    return SpsaLedgerSchemaError(f"Unsupported SPSA ledger schema: {detail}. {_REPAIR_GUIDANCE}")


def canonical_table_names() -> Iterable[str]:
    """Return canonical table names for diagnostics and tests."""

    return tuple(_EXPECTED_COLUMNS)


__all__ = [
    "APPLICATION_SCHEMA_ID",
    "CURRENT_SPSA_LEDGER_SCHEMA_VERSION",
    "SpsaLedgerSchemaError",
    "canonical_table_names",
    "create_canonical_schema",
    "has_managed_tables",
    "read_user_version",
    "schema_digest",
    "stamp_user_version",
    "validate_canonical_schema",
]
