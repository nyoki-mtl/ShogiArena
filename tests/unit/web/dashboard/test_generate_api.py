from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from shogiarena._core.interfaces.dashboard.generate.api import GenerateAPI


class _RuntimeSupportStub:
    def load_run_state(self, _run_dir: Path, **_kwargs: Any) -> dict[str, object]:
        return {
            "schema_version": 2,
            "schedule_hash": "schedule",
            "resume_hash": "resume",
        }


@pytest.mark.asyncio
async def test_generate_summary_reads_records_output_from_manifest(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    records_dir = tmp_path / "records"
    run_dir.mkdir()
    records_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "provenance_sealed",
                "hashes": {"resume_hash": "resume"},
                "schedule": {"kind": "generate"},
                "records_output": {
                    "output_dir": str(records_dir),
                    "format": "sfen",
                    "file_prefix": "selfplay",
                },
                "rules": {"time_control": {"byoyomi": 1000}},
            }
        ),
        encoding="utf-8",
    )
    (records_dir / "records_manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {"path": "a.sfen", "games": 2, "positions": 20, "byte_count": 200},
                    {"path": "b.sfen", "games": 3, "positions": 30, "bytes": 300},
                ]
            }
        ),
        encoding="utf-8",
    )
    api = GenerateAPI(db_path=run_dir / "game.db", run_dir=run_dir, runtime_support=_RuntimeSupportStub())

    response = await api.get_summary(None)  # type: ignore[arg-type]
    payload = json.loads(response.text)

    assert payload["totalGames"] == 5
    assert payload["totalPositions"] == 50
    assert payload["totalBytes"] == 500
    assert payload["recordFormat"] == "sfen"
    assert payload["outputDir"] == str(records_dir)
    assert payload["filePrefix"] == "selfplay"
    assert payload["rules"] == {"time_control": {"byoyomi": 1000}}
    assert payload["runStatus"] == "provenance_sealed"
    assert payload["isResumable"] is True
