from pathlib import Path

import pytest

from shogiarena._core.contexts.spsa.application.param_io import (
    ParamEntry,
    quantize_value,
    read_params,
)


def _write_param_file(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "params.txt"
    path.write_text(content, encoding="utf-8")
    return path


def test_read_params_accepts_valid_entry(tmp_path: Path) -> None:
    path = _write_param_file(tmp_path, "p, int, 4, 1, 8, 2, 0.002\n")
    entries = read_params(path)
    assert len(entries) == 1
    assert entries[0].name == "p"
    assert entries[0].step == 2


def test_read_params_rejects_duplicate_name(tmp_path: Path) -> None:
    path = _write_param_file(tmp_path, "p, int, 4, 1, 8, 2, 0.002\np, int, 5, 1, 8, 2, 0.002\n")
    with pytest.raises(ValueError, match="duplicate"):
        read_params(path)


def test_read_params_rejects_non_finite_value(tmp_path: Path) -> None:
    path = _write_param_file(tmp_path, "p, float, nan, 1, 8, 2, 0.002\n")
    with pytest.raises(ValueError, match="finite"):
        read_params(path)


def test_read_params_rejects_unknown_type(tmp_path: Path) -> None:
    path = _write_param_file(tmp_path, "p, double, 4, 1, 8, 2, 0.002\n")
    with pytest.raises(ValueError, match="type"):
        read_params(path)


def test_read_params_rejects_min_greater_than_max(tmp_path: Path) -> None:
    path = _write_param_file(tmp_path, "p, int, 4, 8, 1, 2, 0.002\n")
    with pytest.raises(ValueError, match="max"):
        read_params(path)


def test_read_params_rejects_non_positive_step_for_used_param(tmp_path: Path) -> None:
    path = _write_param_file(tmp_path, "p, float, 4, 1, 8, 0, 0.002\n")
    with pytest.raises(ValueError, match="step"):
        read_params(path)


def test_read_params_rejects_negative_delta_for_used_param(tmp_path: Path) -> None:
    path = _write_param_file(tmp_path, "p, float, 4, 1, 8, 2, -0.002\n")
    with pytest.raises(ValueError, match="delta"):
        read_params(path)


def test_quantize_value_int_rounds_to_int_and_clamps() -> None:
    p = ParamEntry(
        name="IntParam",
        type="int",
        value=0.0,
        min=0.0,
        max=10.0,
        step=3.0,
        delta=1.0,
        comment="",
        is_not_used=False,
    )
    # Near 4 should snap to nearest integer
    assert quantize_value(p, 3.6) == 4.0
    # Above 10 clamps to 10
    assert quantize_value(p, 11.0) == 10.0
