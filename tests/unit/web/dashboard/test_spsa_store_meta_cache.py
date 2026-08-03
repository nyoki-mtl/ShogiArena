"""`meta.json` は summary / params / progress の各経路から呼ばれるため、
呼び出しごとの再パースを避けつつ、内容が変わったら確実に追随する必要がある。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from shogiarena._core.contexts.dashboard.application.spsa.data_store import SpsaStore


def _write_meta(run_dir: Path, payload: dict[str, object]) -> Path:
    spsa_dir = run_dir / "spsa"
    spsa_dir.mkdir(parents=True, exist_ok=True)
    meta_path = spsa_dir / "meta.json"
    meta_path.write_text(json.dumps(payload), encoding="utf-8")
    return meta_path


def test_repeated_loads_reuse_the_parsed_meta(tmp_path: Path, monkeypatch) -> None:
    _write_meta(tmp_path, {"experiment_name": "exp", "initial_params": {"a": 1.0}})
    store = SpsaStore(run_dir=tmp_path)

    reads: list[Path] = []
    original = SpsaStore.load_json_file

    def _counting_load(path: Path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(SpsaStore, "load_json_file", staticmethod(_counting_load))

    first = store.load_meta_data()
    second = store.load_meta_data()

    assert first.experiment_name == "exp"
    assert second.experiment_name == "exp"
    assert len(reads) == 1, "an unchanged meta.json must not be re-parsed on every call"


def test_rewritten_meta_is_picked_up(tmp_path: Path) -> None:
    meta_path = _write_meta(tmp_path, {"experiment_name": "before"})
    store = SpsaStore(run_dir=tmp_path)
    assert store.load_meta_data().experiment_name == "before"

    _write_meta(tmp_path, {"experiment_name": "after", "num_updates": 7})
    # Force a distinct mtime so the test does not depend on filesystem timestamp resolution.
    stat = meta_path.stat()
    os.utime(meta_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    assert store.load_meta_data().experiment_name == "after"


def test_missing_meta_returns_empty_model(tmp_path: Path) -> None:
    store = SpsaStore(run_dir=tmp_path)
    assert store.load_meta_data().experiment_name is None
