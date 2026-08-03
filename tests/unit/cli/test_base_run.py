from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pytest

from shogiarena._core.interfaces.cli.run.base_run import BaseRunCommand


def test_announce_run_directory_logs_absolute_path(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    command = BaseRunCommand(argparse.Namespace())
    run_dir = tmp_path / "output" / "run"

    with caplog.at_level(logging.INFO):
        command.announce_run_directory(run_dir)

    assert f"Run directory: {run_dir.resolve()}" in caplog.text
