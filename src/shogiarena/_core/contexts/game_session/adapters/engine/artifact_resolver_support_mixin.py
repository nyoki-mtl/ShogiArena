"""Filesystem staging, hashing, and subprocess helpers for artifact builds."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import types
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

import yaml

from shogiarena._core.platform.file_locking import FileLock
from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.settings_loading.settings_models import RepoSettings

from .artifact_resolver_types import ArtifactId

_SEEN_FOUND_KEYS: set[str] = set()
_STREAM_SYS: types.ModuleType = sys
logger = logging.getLogger(__name__)


class ArtifactResolverBuildSupportMixin:
    @staticmethod
    def _build_lock(engine_root: Path, art: ArtifactId) -> FileLock:
        safe = re.sub(r"[^A-Za-z0-9._-]", "-", f"{art.engine}_{art.commit}")
        lock_path = engine_root / ".build-locks" / f"{safe}.lock"
        return FileLock(lock_path, timeout=7200.0, poll_interval=0.5)

    def _lookup_artifact(
        self,
        engine_root: Path,
        art: ArtifactId,
        artifact_id: str,
    ) -> Path | None:
        artifact_name = "artifact.exe" if sys.platform.startswith("win") else "artifact"
        candidate = engine_root / artifact_id / artifact_name
        if candidate.exists() and os.access(candidate, os.X_OK):
            key = f"{art.engine}|{art.commit}|{artifact_id}"
            if key in _SEEN_FOUND_KEYS:
                logger.debug("[artifact] found: %s -> %s", art.engine, candidate)
            else:
                logger.info("[artifact] found: %s -> %s", art.engine, candidate)
                _SEEN_FOUND_KEYS.add(key)
            return candidate
        return None

    @staticmethod
    def _artifact_hash(
        art: ArtifactId,
        *,
        repo: RepoSettings,
        opts: Mapping[str, JsonValue],
        build_config_sha256: str,
        tune_file_sha256: str,
    ) -> str:
        platform_tag = {
            "os": os.name,
            "platform": sys.platform,
            "machine": platform.machine(),
        }
        payload = {
            "engine": art.engine,
            "commit": art.commit,
            "repo": repo.name,
            "build_config_sha256": build_config_sha256,
            "tune_file_sha256": tune_file_sha256,
            "platform": platform_tag,
            "opts": opts,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _file_md5(path: Path) -> str:
        digest = hashlib.md5()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _stage_artifact(
        self,
        engine_root: Path,
        art: ArtifactId,
        repo: RepoSettings,
        artifact_id: str,
        build_opts: Mapping[str, JsonValue],
        candidate: Path,
        commands: list[list[str]],
    ) -> Path:
        dest_dir = engine_root / artifact_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        artifact_name = "artifact.exe" if sys.platform.startswith("win") else "artifact"
        dest_path = dest_dir / artifact_name
        shutil.copy2(str(candidate), str(dest_path))

        info = {
            "engine": art.engine,
            "commit": art.commit,
            "repo": {"name": repo.name, "path": str(repo.path), "url": repo.url},
            "artifact_id": artifact_id,
            "artifact": {
                "name": candidate.name,
                "path": str(dest_path),
                "md5": self._file_md5(dest_path),
                "size_bytes": dest_path.stat().st_size,
            },
            "build_config_sha256": self._file_sha256(repo.build_config) if repo.build_config else "",
            "build_options": dict(build_opts),
            "commands": commands,
        }
        info_path = dest_dir / "build_info.yaml"
        with open(info_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(info, handle, allow_unicode=True, sort_keys=False)

        logger.info("[artifact] staged: %s -> %s", candidate, dest_path)
        return dest_path

    @staticmethod
    def _run_logged_subprocess(
        cmd: Sequence[str],
        *,
        cwd: Path,
        log_file: TextIO,
        env: dict[str, str] | None,
        should_stream: bool,
    ) -> None:
        command_line = f"$ {' '.join(cmd)}\n"
        log_file.write(command_line)
        log_file.flush()
        if should_stream:
            _STREAM_SYS.stdout.write(command_line)
            _STREAM_SYS.stdout.flush()
            proc = subprocess.Popen(
                list(cmd),
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            for chunk in proc.stdout:
                log_file.write(chunk)
                log_file.flush()
                _STREAM_SYS.stdout.write(chunk)
                _STREAM_SYS.stdout.flush()
            ret = proc.wait()
            if ret:
                raise subprocess.CalledProcessError(ret, cmd)
            return
        subprocess.check_call(list(cmd), cwd=str(cwd), stdout=log_file, stderr=subprocess.STDOUT, env=env)

    @staticmethod
    def _git_env_with_safe_directory(repo_root: Path) -> dict[str, str]:
        env = os.environ.copy()
        try:
            resolved = repo_root.resolve(strict=False)
        except OSError as exc:
            logger.debug("Failed to resolve repo_root=%s; using raw path: %s", repo_root, exc)
            resolved = repo_root
        repo_path = str(resolved)
        key_prefix = "GIT_CONFIG_KEY_"
        val_prefix = "GIT_CONFIG_VALUE_"
        count_raw = env.get("GIT_CONFIG_COUNT", "0")
        try:
            base = int(count_raw)
        except ValueError as exc:
            logger.debug("Invalid GIT_CONFIG_COUNT=%r; falling back to 0: %s", count_raw, exc)
            base = 0
        for idx in range(base):
            if env.get(f"{key_prefix}{idx}") == "safe.directory" and env.get(f"{val_prefix}{idx}") == repo_path:
                return env
        env["GIT_CONFIG_COUNT"] = str(base + 1)
        env[f"{key_prefix}{base}"] = "safe.directory"
        env[f"{val_prefix}{base}"] = repo_path
        return env

    @staticmethod
    def _stream_build_logs_enabled() -> bool:
        flag = os.environ.get("SHOGIARENA_STREAM_BUILD_LOGS", "")
        return flag.strip().lower() in {"1", "true", "yes", "on"}
