"""Public CLI subprocess coverage for SPSA cancellation and durable resume."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import yaml

_ENGINE_SOURCE = """\
#!/usr/bin/env python3
import json
import sys
import time
from pathlib import Path

RUN_DIR = Path(__RUN_DIR__)
FIRST_GO = Path(__FIRST_GO__)
RESUME_ARMED = Path(__RESUME_ARMED__)
RESUME_OBSERVATION = Path(__RESUME_OBSERVATION__)


def send(line):
    sys.stdout.write(line + "\\n")
    sys.stdout.flush()


while True:
    raw = sys.stdin.readline()
    if not raw:
        break
    line = raw.strip()
    if not line:
        continue
    if line == "usi":
        send("id name SpsaCliResumeMock")
        send("option name ParamA type spin default 10 min 0 max 20")
        send("option name Clear Hash type button")
        send("usiok")
    elif line == "usi_tunables":
        send(
            'info string shogiarena_tunables_json '
            '{"schema_version":"shogiarena.usi_tunables.v1","tunables":['
            '{"id":"ParamA","option":"ParamA","value_type":"int",'
            '"encoding":"integer","default":10,"min":0,"max":20,'
            '"schedule":{"c_end":1,"r_end":1}}]}'
        )
        send("usi_tunablesok")
    elif line == "isready":
        send("readyok")
    elif line.startswith("go"):
        FIRST_GO.touch()
        if RESUME_ARMED.exists():
            RESUME_OBSERVATION.write_text(
                json.dumps(
                    {
                        "completion_status": (RUN_DIR / "completion_status.json").exists(),
                        "terminal": (RUN_DIR / "spsa" / "terminal.json").exists(),
                        "terminal_provisional": (RUN_DIR / "spsa" / "terminal.provisional.json").exists(),
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        time.sleep(0.5)
        send("bestmove resign")
    elif line == "stop":
        send("bestmove resign")
    elif line == "quit":
        break
"""


def _write_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    run_dir = root / "run"
    engine_dir = root / "engine"
    engine_dir.mkdir()
    first_go = root / "first-go.flag"
    resume_armed = root / "resume-armed.flag"
    resume_observation = root / "resume-observation.json"
    script = engine_dir / "mock_engine.py"
    script.write_text(
        textwrap.dedent(_ENGINE_SOURCE)
        .replace("__RUN_DIR__", repr(str(run_dir)))
        .replace("__FIRST_GO__", repr(str(first_go)))
        .replace("__RESUME_ARMED__", repr(str(resume_armed)))
        .replace("__RESUME_OBSERVATION__", repr(str(resume_observation))),
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    launcher = script
    if sys.platform == "win32":
        launcher = script.with_suffix(".bat")
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")

    engine_config = engine_dir / "engine.yaml"
    engine_config.write_text(
        yaml.safe_dump({"name": "SPSA CLI Resume Mock", "engine_path": launcher.as_posix()}),
        encoding="utf-8",
    )
    (root / "positions.sfen").write_text("startpos\n", encoding="utf-8")
    (root / "space.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "shogiarena.spsa.space.v1",
                "target": {
                    "engine_family": "test",
                    "protocol": "usi_options",
                    "required_options_policy": "strict",
                    "tunable_manifest": {"required": True, "command": "usi_tunables"},
                },
                "parameters": [
                    {
                        "id": "ParamA",
                        "target": {"option": "ParamA", "value_encoding": "integer"},
                        "value_type": "int",
                        "initial": 10,
                        "bounds": {"min": 0, "max": 20},
                        "schedule": {"c_end": 1, "r_end": 1},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = root / "spsa.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "experiment_name": "cli-resume",
                "engines": [{"name": "SPSA CLI Resume Mock", "engine_path": engine_config.as_posix()}],
                "dashboard": {"enabled": False},
                "rules": {
                    "initial_positions": {
                        "type": "file",
                        "source": "positions.sfen",
                        "flip_policy": "pair_both",
                    },
                    "adjudication": {
                        "enable_max_plies": True,
                        "max_plies": 2,
                        "sync_max_plies_with_engine": False,
                    },
                    "time_control": {"time_ms": 5000, "increment_ms": 0},
                },
                "spsa": {
                    "space": "space.yaml",
                    "run_seed": "11" * 32,
                    "num_updates": 1,
                    "pairs_per_update": 1,
                    "inflight_factor": 1,
                    "num_parallel": 1,
                },
                "system": {
                    "engine_handshake_timeout": 5,
                    "path_preflight": "off",
                    "resource_capacity_preflight": "off",
                },
            }
        ),
        encoding="utf-8",
    )
    return config, run_dir, first_go, resume_observation


def _cli_executable() -> Path:
    suffix = ".exe" if sys.platform == "win32" else ""
    executable = Path(sys.executable).with_name(f"shogiarena{suffix}")
    if not executable.is_file():
        raise AssertionError(f"shogiarena console script is unavailable: {executable}")
    return executable


def _command(root: Path, config: Path, run_dir: Path, *, fresh: bool) -> list[str]:
    command = [
        str(_cli_executable()),
        "--output-dir",
        str(root / "output"),
        "run",
        "spsa",
        str(config),
        "--run-dir",
        str(run_dir),
    ]
    if fresh:
        command.append("--no-resume")
    return command


def _wait_for_file(process: subprocess.Popen[str], path: Path, *, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if process.poll() is not None:
            output, _ = process.communicate()
            raise AssertionError(f"SPSA CLI exited before {path.name} was created:\n{output}")
        time.sleep(0.05)
    raise AssertionError(f"SPSA CLI did not create {path} within {timeout:.1f}s")


def test_public_cli_cancel_resume_and_complete(tmp_path: Path) -> None:
    config, run_dir, first_go, resume_observation = _write_fixture(tmp_path)
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    environment["SHOGI_ARENA_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS"] = "10"
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    process = subprocess.Popen(
        _command(tmp_path, config, run_dir, fresh=True),
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags,
    )
    _wait_for_file(process, first_go)
    process.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
    cancelled_output, _ = process.communicate(timeout=20)
    assert process.returncode in {0, 130}, cancelled_output

    cancelled_terminal = json.loads((run_dir / "spsa" / "terminal.json").read_text(encoding="utf-8"))
    assert cancelled_terminal["reason"] == "cancelled_resumable"
    assert (run_dir / "completion_status.json").is_file()
    assert (run_dir / "spsa" / "terminal.provisional.json").is_file()

    first_go.unlink()
    (tmp_path / "resume-armed.flag").touch()
    resumed = subprocess.run(
        _command(tmp_path, config, run_dir, fresh=False),
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert first_go.is_file()
    invalidation_observation = json.loads(resume_observation.read_text(encoding="utf-8"))
    assert invalidation_observation == {
        "completion_status": False,
        "terminal": False,
        "terminal_provisional": False,
    }
    completion = json.loads((run_dir / "completion_status.json").read_text(encoding="utf-8"))
    terminal = json.loads((run_dir / "spsa" / "terminal.json").read_text(encoding="utf-8"))
    assert completion["status"] == "clean"
    assert completion["termination_reason"] == "completed"
    assert terminal["reason"] == "completed"
    assert (run_dir / "completed.flag").is_file()
