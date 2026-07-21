"""Shared utilities for CLI commands that manage runtime directories."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse, urlunparse

LOGGER = logging.getLogger("shogiarena.cli.setup")


def clone_repo(remote: str, target_dir: Path, commit: str | None = None, *, token: str | None = None) -> None:
    """Clone a git repository into ``target_dir`` (idempotent)."""

    if target_dir.exists() and any(target_dir.iterdir()):
        LOGGER.info("Repo directory %s already exists; skipping clone", target_dir)
        return

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Cloning repo from %s to %s", _mask_remote(remote), target_dir)
    try:
        _run_git_clone(remote, target_dir, token=token)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"git clone failed: exit status {exc.returncode}") from exc

    if commit:
        LOGGER.info("Checking out %s", commit)
        try:
            subprocess.run(["git", "checkout", commit], cwd=str(target_dir), check=True)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"git checkout {commit} failed: {exc}") from exc

    LOGGER.info("Repository ready at %s", target_dir)


def _run_git_clone(remote: str, target_dir: Path, *, token: str | None) -> None:
    if not token or not _should_use_github_token(remote):
        subprocess.run(["git", "clone", remote, str(target_dir)], check=True)
        return

    with tempfile.TemporaryDirectory(prefix="arena_git_askpass_") as tmp_dir:
        askpass_path = _write_askpass_helper(Path(tmp_dir))
        env = dict(os.environ)
        env["GIT_ASKPASS"] = str(askpass_path)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["SHOGIARENA_GIT_TOKEN"] = token
        subprocess.run(["git", "clone", remote, str(target_dir)], env=env, check=True)


def _write_askpass_helper(tmp_dir: Path) -> Path:
    """GIT_ASKPASS ヘルパを生成する。

    トークンは引数ではなく環境変数 ``SHOGIARENA_GIT_TOKEN`` 経由で渡し、
    プロセス一覧に露出しないようにする。Windows では ``.sh`` を直接起動できないため、
    Python 実装を ``.bat`` から呼び出す。

    Args:
        tmp_dir: ヘルパを書き出す一時ディレクトリ。

    Returns:
        ``GIT_ASKPASS`` に設定するヘルパのパス。
    """
    if sys.platform.startswith("win"):
        helper_py = tmp_dir / "askpass.py"
        helper_py.write_text(
            "import os, sys\n"
            'prompt = sys.argv[1] if len(sys.argv) > 1 else ""\n'
            'if "Username" in prompt:\n'
            '    print("x-access-token")\n'
            "else:\n"
            '    print(os.environ.get("SHOGIARENA_GIT_TOKEN", ""))\n',
            encoding="utf-8",
        )
        helper_bat = tmp_dir / "askpass.bat"
        helper_bat.write_text(
            f'@echo off\r\n"{sys.executable}" "{helper_py}" %*\r\n',
            encoding="utf-8",
        )
        return helper_bat

    helper_sh = tmp_dir / "askpass.sh"
    helper_sh.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        '  *Username*) printf "%s\\n" "x-access-token" ;;\n'
        '  *) printf "%s\\n" "$SHOGIARENA_GIT_TOKEN" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    helper_sh.chmod(0o700)
    return helper_sh


def _should_use_github_token(remote: str) -> bool:
    parsed = urlparse(remote)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.hostname or ""
    return host == "github.com" or host.endswith(".github.com")


def _mask_remote(remote: str) -> str:
    parsed = urlparse(remote)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        netloc = parsed.hostname
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))
    return remote
