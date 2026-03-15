from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "tools" / "detect_architecture_imports.py"


def _load_architecture_lint_module():
    spec = importlib.util.spec_from_file_location("detect_architecture_imports_tool", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"failed to load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ARCHITECTURE_LINT = _load_architecture_lint_module()


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_arch_lint(root: Path) -> tuple[int, str]:
    stdout = io.StringIO()
    argv = sys.argv[:]
    try:
        sys.argv = [str(SCRIPT_PATH), "--root", str(root), "--fail-on-violations"]
        with redirect_stdout(stdout):
            exit_code = ARCHITECTURE_LINT.main()
    finally:
        sys.argv = argv
    return exit_code, stdout.getvalue()


def test_unallowlisted_interfaces_to_legacy_arena_import_is_violation(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "shogiarena/_core/interfaces/dashboard/unlisted.py",
        "from shogiarena.arena.services.statistics.sprt import value\n",
    )
    _write_file(tmp_path / "shogiarena/arena/services/statistics/sprt.py", "value = 1\n")

    exit_code, stdout = _run_arch_lint(tmp_path)

    assert exit_code == 1
    assert "legacy.arena" in stdout
    assert "unlisted.py" in stdout


def test_interfaces_to_legacy_arena_entrypoints_import_is_violation(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "shogiarena/_core/interfaces/dashboard/instances/api.py",
        "from shogiarena.arena.instances.entrypoints import InstancePool\n",
    )
    _write_file(tmp_path / "shogiarena/arena/instances/entrypoints.py", "class InstancePool: ...\n")

    exit_code, stdout = _run_arch_lint(tmp_path)

    assert exit_code == 1
    assert "legacy.arena" in stdout
    assert "instances/api.py" in stdout


def test_interfaces_to_legacy_web_import_is_violation(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "shogiarena/_core/interfaces/dashboard/sample.py",
        "from shogiarena.web.dashboard.api_server import value\n",
    )
    _write_file(tmp_path / "shogiarena/web/dashboard/api_server.py", "value = 1\n")

    exit_code, stdout = _run_arch_lint(tmp_path)

    assert exit_code == 1
    assert "legacy.web" in stdout
    assert "sample.py" in stdout


def test_type_checking_interfaces_to_legacy_arena_import_is_violation(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "shogiarena/_core/interfaces/dashboard/sample.py",
        (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from shogiarena.arena.services.statistics.sprt import Sprt\n"
        ),
    )
    _write_file(tmp_path / "shogiarena/arena/services/statistics/sprt.py", "class Sprt: ...\n")

    exit_code, stdout = _run_arch_lint(tmp_path)

    assert exit_code == 1
    assert "legacy.arena" in stdout
    assert "sample.py" in stdout


def test_type_checking_legacy_entrypoints_import_is_violation(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "shogiarena/_core/interfaces/dashboard/instances/api.py",
        (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from shogiarena.arena.instances.entrypoints import InstancePool\n"
        ),
    )
    _write_file(tmp_path / "shogiarena/arena/instances/entrypoints.py", "class InstancePool: ...\n")

    exit_code, stdout = _run_arch_lint(tmp_path)

    assert exit_code == 1
    assert "legacy.arena" in stdout
    assert "instances/api.py" in stdout


def test_context_ports_to_session_runtime_is_allowed(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "shogiarena/_core/contexts/tournament/ports/runtime.py",
        "from shogiarena._core.contexts.game_session.ports.session_runtime import SessionRuntimePort\n",
    )
    _write_file(
        tmp_path / "shogiarena/_core/contexts/game_session/ports/session_runtime.py",
        "class SessionRuntimePort: ...\n",
    )

    exit_code, stdout = _run_arch_lint(tmp_path)

    assert exit_code == 0
    assert "no violations" in stdout


def test_context_ports_to_non_substrate_game_session_port_is_violation(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "shogiarena/_core/contexts/tournament/ports/runtime.py",
        "from shogiarena._core.contexts.game_session.ports.session_runner_ports import BaseSessionRunner\n",
    )
    _write_file(
        tmp_path / "shogiarena/_core/contexts/game_session/ports/session_runner_ports.py",
        "class BaseSessionRunner: ...\n",
    )

    exit_code, stdout = _run_arch_lint(tmp_path)

    assert exit_code == 1
    assert "session_runner_ports" in stdout
    assert "runtime.py" in stdout


def test_context_application_to_session_runtime_is_violation(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "shogiarena/_core/contexts/tournament/application/usecase.py",
        "from shogiarena._core.contexts.game_session.ports.session_runtime import SessionRuntimePort\n",
    )
    _write_file(
        tmp_path / "shogiarena/_core/contexts/game_session/ports/session_runtime.py",
        "class SessionRuntimePort: ...\n",
    )

    exit_code, stdout = _run_arch_lint(tmp_path)

    assert exit_code == 1
    assert "session_runtime" in stdout
    assert "usecase.py" in stdout
