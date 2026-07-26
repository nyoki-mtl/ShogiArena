"""timeout 4経路が共通 helper を通ることの source contract（task 0052 / Decision 5）。

到達可能な経路は実 subprocess の smoke で検証するが、「新しい timeout 分岐が helper を
迂回して直接 ``timeout_win_result`` を返す」退行は静的に固定しておく。
"""

from __future__ import annotations

import ast
from pathlib import Path

import shogiarena

_MATCH_APPLICATION_DIR = Path(shogiarena.__file__).parent / "_core" / "contexts" / "match" / "application"
_LOOP_MIXIN = _MATCH_APPLICATION_DIR / "runner_loop_mixin.py"
_MOVE_MIXIN = _MATCH_APPLICATION_DIR / "runner_move_mixin.py"

_EXPECTED_SITES = {
    "loop_top_expired",
    "wait_for_timeout",
    "recovered_bestmove_expired",
    "update_after_move_expired",
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _timeout_helper_call_sites(tree: ast.Module) -> set[str]:
    sites: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "_timeout_result_or_error":
            continue
        for keyword in node.keywords:
            if keyword.arg == "site" and isinstance(keyword.value, ast.Constant):
                sites.add(str(keyword.value.value))
    return sites


def _direct_timeout_win_calls(tree: ast.Module) -> list[str]:
    """``_timeout_result_or_error`` の外から ``timeout_win_result`` を呼んでいる関数名。"""

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name == "_timeout_result_or_error":
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "timeout_win_result"
            ):
                offenders.append(node.name)
                break
    return offenders


def test_all_four_timeout_sites_go_through_the_shared_helper() -> None:
    sites = _timeout_helper_call_sites(_parse(_LOOP_MIXIN)) | _timeout_helper_call_sites(_parse(_MOVE_MIXIN))
    assert sites == _EXPECTED_SITES


def test_no_timeout_path_bypasses_the_shared_helper() -> None:
    offenders = _direct_timeout_win_calls(_parse(_LOOP_MIXIN)) + _direct_timeout_win_calls(_parse(_MOVE_MIXIN))
    assert offenders == [], f"timeout_win_result must only be called from the shared helper: {offenders}"


def test_every_site_passes_a_deadline_snapshot_from_the_game_clock() -> None:
    """外側で budget を再計算せず、GameClock の snapshot を渡していること。"""

    sources = (_LOOP_MIXIN.read_text(encoding="utf-8"), _MOVE_MIXIN.read_text(encoding="utf-8"))
    combined = "\n".join(sources)
    # 旧実装が使っていた「窓と予算を別々に渡す」引数が残っていないこと。
    assert "budget_ms=" not in combined
    assert "last_charged_window" not in combined
    assert "last_expiry_budget_ms" not in combined
