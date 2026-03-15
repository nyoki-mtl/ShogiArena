"""Shared utilities for CLI commands that manage runtime directories."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse

LOGGER = logging.getLogger("shogiarena.cli.setup")


def clone_repo(remote: str, target_dir: Path, commit: str | None = None, *, token: str | None = None) -> None:
    """Clone a git repository into ``target_dir`` (idempotent)."""

    if target_dir.exists() and any(target_dir.iterdir()):
        LOGGER.info("Repo directory %s already exists; skipping clone", target_dir)
        return

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    clone_remote = _inject_token(remote, token)
    LOGGER.info("Cloning repo from %s to %s", _mask_remote(remote), target_dir)
    try:
        subprocess.run(["git", "clone", clone_remote, str(target_dir)], check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"git clone failed: {exc}") from exc

    if commit:
        LOGGER.info("Checking out %s", commit)
        try:
            subprocess.run(["git", "checkout", commit], cwd=str(target_dir), check=True)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"git checkout {commit} failed: {exc}") from exc

    LOGGER.info("Repository ready at %s", target_dir)


def _inject_token(remote: str, token: str | None) -> str:
    if not token:
        return remote
    parsed = urlparse(remote)
    if parsed.scheme not in {"http", "https"}:
        return remote
    if parsed.hostname and parsed.hostname.endswith("github.com"):
        netloc = f"x-access-token:{token}@{parsed.hostname}"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))
    return remote


def _mask_remote(remote: str) -> str:
    parsed = urlparse(remote)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        netloc = parsed.hostname
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))
    return remote
