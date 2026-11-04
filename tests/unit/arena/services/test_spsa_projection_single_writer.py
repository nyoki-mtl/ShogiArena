from __future__ import annotations

import json
import threading

from shogiarena._core.contexts.spsa.adapters import ledger_projection
from shogiarena._core.contexts.spsa.adapters.runtime.persistence import log_runtime_event, record_ltc_result
from tests.unit.arena.services.test_spsa_ledger_projection import _commit_update, _runtime


def test_runtime_diagnostics_do_not_write_projector_files_during_projection(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    errors = []
    original_write = ledger_projection.write_json_atomic

    def slow(path, payload):
        if path.name == "index.json":
            entered.set()
            assert release.wait(5)
        return original_write(path, payload)

    monkeypatch.setattr(ledger_projection, "write_json_atomic", slow)

    def project():
        connection, runtime = _runtime()
        try:
            _commit_update(connection, runtime)
            runtime.project_derived_json(run_dir=tmp_path)
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=project)
    thread.start()
    try:
        assert entered.wait(5)
        log_runtime_event(tmp_path, "session", {"event": "game_scheduled"})
        record_ltc_result(
            run_dir=tmp_path,
            session_uuid="session",
            results_path=tmp_path / "spsa/ltc/results.jsonl",
            record={"update_idx": 1, "status": "passed"},
        )
        assert not (tmp_path / "spsa/index.json").exists()
        assert not (tmp_path / "spsa/events.jsonl").exists()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and errors == []
    index = json.loads((tmp_path / "spsa/index.json").read_text(encoding="utf-8"))
    assert index["metadata"]["projection_source"] == "spsa/ledger.sqlite3"
    assert json.loads((tmp_path / "spsa/ltc/results.jsonl").read_text())["status"] == "passed"
