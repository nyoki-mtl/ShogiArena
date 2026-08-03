from __future__ import annotations

from pathlib import Path

from shogiarena._core.shared.kernel.run_paths import latest_run_dir_for_key, run_group_dir_for_key


def test_latest_run_dir_for_key_uses_exact_schedule_group(tmp_path: Path) -> None:
    expected_group = run_group_dir_for_key(tmp_path, "tournament", "abcdef1234567890")
    older = expected_group / "20260801010101"
    latest = expected_group / "20260802020202"
    older.mkdir(parents=True)
    latest.mkdir()
    other_group = run_group_dir_for_key(tmp_path, "tournament", "9999999999999999")
    (other_group / "20260803030303").mkdir(parents=True)

    assert latest_run_dir_for_key(tmp_path, "tournament", "abcdef1234567890") == latest


def test_latest_run_dir_for_key_returns_none_without_timestamped_run(tmp_path: Path) -> None:
    group = run_group_dir_for_key(tmp_path, "tournament", "abcdef1234567890")
    (group / "notes").mkdir(parents=True)

    assert latest_run_dir_for_key(tmp_path, "tournament", "abcdef1234567890") is None
