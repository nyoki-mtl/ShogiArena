from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

_CRASH_EXIT = 87


def main() -> None:
    ledger_path = Path(sys.argv[1])
    connection = sqlite3.connect(ledger_path)
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA cache_size=4")
    connection.execute("PRAGMA cache_spill=ON")
    connection.execute("BEGIN IMMEDIATE")
    payload = '{"padding":"' + ("x" * 4096) + '"}'
    connection.executemany(
        """
        INSERT INTO event_revisions (run_id, revision, event_type, payload_json, created_at)
        VALUES ('run-1', ?, 'crash_fixture', ?, '2026-01-01T00:00:00+00:00')
        """,
        ((revision, payload) for revision in range(1, 513)),
    )
    os._exit(_CRASH_EXIT)


if __name__ == "__main__":
    main()
