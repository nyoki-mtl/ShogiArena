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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TextIO

import yaml

from shogiarena.arena.tuning.spsa_tune import read_tune_file, tune_parameters
from shogiarena.utils.common import project_dirs
from shogiarena.utils.common.locks import FileLock
from shogiarena.utils.common.paths import resolve_path_like
from shogiarena.utils.common.settings import RepoSettings
from shogiarena.utils.cpuinfo import detect_target_cpu

logger = logging.getLogger(__name__)


_SEEN_FOUND_KEYS: set[str] = set()
_OPT_PLACEHOLDER_RE = re.compile(r"\{opts\.([A-Za-z0-9_]+)\}")


@dataclass(frozen=True)
class ArtifactId:
    engine: str
    commit: str

    @classmethod
    def parse(cls, s: str) -> ArtifactId:
        # Strict format: <repo>/<commit_hash>
        raw = s.strip()
        m = re.match(r"^([A-Za-z0-9._-]+)/([A-Fa-f0-9]{6,40})$", raw)
        if not m:
            raise ValueError(f"Invalid artifact id: {s}. Expected '<repo>/<commit_hash>' (hex).")
        return cls(engine=m.group(1), commit=m.group(2))


class ArtifactResolver:
    """Resolve engine artifacts to local binary paths, building on demand."""

    def __init__(self, *, overwrite: bool = False) -> None:
        self.overwrite = overwrite

    def resolve(self, artifact: str, overrides: dict[str, Any] | None = None) -> Path:
        """Return local binary path for `artifact`, building if missing.

        Args:
            artifact: e.g. "yaneuraou/3570416"
        """
        art = ArtifactId.parse(artifact)
        overrides = dict(overrides or {})

        repo = project_dirs.repos.get(art.engine)
        if repo is None:
            raise ValueError(
                f"Unknown repo '{art.engine}' (configure it in settings.yaml). "
                "Run `shogiarena config init` to create settings.yaml, "
                "then use `shogiarena config repo set` to add repositories."
            )
        if not repo.build_config:
            raise ValueError(f"Repo '{art.engine}' has no build_config configured in settings.yaml")
        if not repo.build_config.exists():
            raise FileNotFoundError(f"build_config not found: {repo.build_config}")
        engine_root = project_dirs.engine_dir / art.engine
        engine_root.mkdir(parents=True, exist_ok=True)

        cfg = self._load_build_config(repo.build_config)
        build_config_sha256 = self._file_sha256(repo.build_config)
        build_opts = self._prepare_build_opts(cfg, overrides)
        ctx, _work_dir, _source_dir, _git_root = self._build_context(cfg, repo, art, build_opts, engine_root)
        tune_file = self._resolve_tune_file(cfg, build_opts, ctx)
        tune_hash = self._tune_file_sha256(tune_file)
        hash_opts = self._select_hash_opts(cfg, build_opts, tune_file)
        artifact_id = self._artifact_hash(
            art,
            repo=repo,
            opts=hash_opts,
            build_config_sha256=build_config_sha256,
            tune_file_sha256=tune_hash,
        )

        candidate = self._lookup_artifact(engine_root, art, artifact_id)
        if candidate is not None:
            return candidate

        lock = self._build_lock(engine_root, art)
        with lock:
            candidate = self._lookup_artifact(engine_root, art, artifact_id)
            if candidate is not None:
                return candidate

            cpu_label = build_opts.get("target_cpu", "-")
            edition_label = build_opts.get("edition", "-")
            tune_tag_label = build_opts.get("tune_tag", "-")
            logger.info(
                "[artifact] %s (cpu=%s, edition=%s, tag=%s) not found; starting build...",
                artifact,
                cpu_label,
                edition_label,
                tune_tag_label,
            )
            bin_path = self._build_from_yaml(
                engine_root,
                art,
                build_opts,
                repo=repo,
                artifact_id=artifact_id,
                cfg=cfg,
            )
            logger.info("[artifact] build complete: %s", bin_path)
            return bin_path

    # --- Internals -----------------------------------------------------
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

    def _load_build_config(self, path: Path) -> Mapping[str, Any]:
        config_path = path.expanduser()
        if not config_path.exists():
            raise FileNotFoundError(f"build_config not found: {config_path}")
        with open(config_path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        if not isinstance(raw, Mapping):
            raise TypeError(f"build_config must be a mapping: {config_path}")
        return raw

    @staticmethod
    def _expand_value(value: Any, ctx: Mapping[str, Any]) -> Any:
        if isinstance(value, str):
            try:
                return value.format_map(ctx)
            except (KeyError, AttributeError) as exc:
                raise ValueError(f"Unknown placeholder in build_config: {value}") from exc
        if isinstance(value, list):
            return [ArtifactResolver._expand_value(item, ctx) for item in value]
        if isinstance(value, dict):
            return {key: ArtifactResolver._expand_value(val, ctx) for key, val in value.items()}
        return value

    @staticmethod
    def _resolve_path(value: str, *, base: Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (base / path).resolve()
        return path

    @staticmethod
    def _config_uses_opt(cfg: Mapping[str, Any], key: str) -> bool:
        needle = f"{{opts.{key}}}"

        def _walk(value: Any) -> bool:
            if isinstance(value, str):
                return needle in value
            if isinstance(value, list):
                return any(_walk(item) for item in value)
            if isinstance(value, dict):
                return any(_walk(val) for val in value.values())
            return False

        return _walk(cfg)

    def _prepare_build_opts(self, cfg: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
        defaults = cfg.get("defaults") or {}
        if not isinstance(defaults, Mapping):
            raise TypeError("build_config.defaults must be a mapping")
        build_opts: dict[str, Any] = {**defaults, **overrides}

        needs_cpu = "target_cpu" in build_opts or self._config_uses_opt(cfg, "target_cpu")
        if needs_cpu:
            cpu = str(build_opts.get("target_cpu") or "").strip()
            if not cpu:
                try:
                    detected_cpu = detect_target_cpu()
                    cpu = str(detected_cpu).strip()
                    logger.debug("[artifact] auto-detected TARGET_CPU=%s", cpu)
                except (OSError, RuntimeError, ValueError) as exc:
                    raise ValueError(
                        "build_options.target_cpu is required to resolve artifacts (auto-detect failed)"
                    ) from exc
            if not cpu:
                raise ValueError("build_options.target_cpu must be a non-empty string")
            build_opts["target_cpu"] = cpu

        needs_edition = "edition" in build_opts or self._config_uses_opt(cfg, "edition")
        if needs_edition:
            edition = str(build_opts.get("edition") or "").strip()
            if not edition:
                raise ValueError("build_options.edition must be a non-empty string")
            build_opts["edition"] = edition

        needs_tag = "tune_tag" in build_opts or self._config_uses_opt(cfg, "tune_tag") or "tune_file" in build_opts
        if needs_tag:
            raw_tag = build_opts.get("tune_tag")
            tune_tag = str(raw_tag).strip() if isinstance(raw_tag, str) and raw_tag else "vanilla"
            tune_tag = re.sub(r"[^A-Za-z0-9._-]", "-", tune_tag)
            build_opts["tune_tag"] = tune_tag

        return build_opts

    def _build_context(
        self,
        cfg: Mapping[str, Any],
        repo: RepoSettings,
        art: ArtifactId,
        build_opts: Mapping[str, Any],
        engine_root: Path,
    ) -> tuple[dict[str, Any], Path, Path, Path]:
        repo_ctx = {
            "path": str(repo.path),
            "name": repo.name,
            "url": repo.url or "",
        }
        opts_ctx = {k: (str(v) if isinstance(v, Path) else v) for k, v in build_opts.items()}
        opts_ctx.setdefault("commit", art.commit)
        opts_ctx.setdefault("engine", art.engine)
        paths_ctx = {
            "engine_root": str(engine_root),
            "engine_dir": str(project_dirs.engine_dir),
            "output_dir": str(project_dirs.output_dir),
        }
        ctx: dict[str, Any] = {
            "repo": SimpleNamespace(**repo_ctx),
            "opts": SimpleNamespace(**opts_ctx),
            "paths": SimpleNamespace(**paths_ctx),
            "engine_root": paths_ctx["engine_root"],
        }

        work_dir_raw = cfg.get("work_dir") or "{repo.path}"
        work_dir_expanded = self._expand_value(work_dir_raw, ctx)
        if not isinstance(work_dir_expanded, str):
            raise TypeError("build_config.work_dir must be a string")
        work_dir = self._resolve_path(work_dir_expanded, base=repo.path)

        ctx["work_dir"] = str(work_dir)

        source_dir_raw = cfg.get("source_dir") or str(work_dir)
        source_dir_expanded = self._expand_value(source_dir_raw, ctx)
        if not isinstance(source_dir_expanded, str):
            raise TypeError("build_config.source_dir must be a string")
        source_dir = self._resolve_path(source_dir_expanded, base=work_dir)

        ctx["source_dir"] = str(source_dir)

        git_cfg = cfg.get("git") or {}
        if not isinstance(git_cfg, Mapping):
            raise TypeError("build_config.git must be a mapping")
        git_root_raw = git_cfg.get("root") or str(source_dir)
        git_root_expanded = self._expand_value(git_root_raw, ctx)
        if not isinstance(git_root_expanded, str):
            raise TypeError("build_config.git.root must be a string")
        git_root = self._resolve_path(git_root_expanded, base=source_dir)

        return ctx, work_dir, source_dir, git_root

    def _resolve_tune_file(
        self,
        cfg: Mapping[str, Any],
        build_opts: Mapping[str, Any],
        ctx: Mapping[str, Any],
    ) -> str | None:
        tune_cfg = cfg.get("tune")
        tune_enabled = True
        if tune_cfg is False:
            tune_enabled = False
        if isinstance(tune_cfg, Mapping):
            tune_enabled = bool(tune_cfg.get("enabled", True))
        if not tune_enabled:
            return None

        if isinstance(tune_cfg, Mapping) and tune_cfg.get("file"):
            tune_file_expanded = self._expand_value(tune_cfg.get("file"), ctx)
            if not isinstance(tune_file_expanded, str):
                raise TypeError("build_config.tune.file must be a string")
            return tune_file_expanded

        tune_file = build_opts.get("tune_file")
        if isinstance(tune_file, str) and tune_file.strip():
            return str(tune_file)
        return None

    @staticmethod
    def _tune_file_sha256(tune_file: str | None) -> str:
        if not tune_file:
            return ""
        try:
            tune_path = Path(resolve_path_like(tune_file))
            if tune_path.exists():
                return ArtifactResolver._file_sha256(tune_path)
        except OSError:
            return ""
        return ""

    def _build_from_yaml(
        self,
        engine_root: Path,
        art: ArtifactId,
        build_opts: Mapping[str, Any],
        *,
        repo: RepoSettings,
        artifact_id: str,
        cfg: Mapping[str, Any],
    ) -> Path:
        if not repo.build_config:
            raise ValueError(f"Repo '{repo.name}' has no build_config configured")
        ctx, work_dir, source_dir, git_root = self._build_context(cfg, repo, art, build_opts, engine_root)

        git_env = self._git_env_with_safe_directory(git_root)
        stream_build_logs = self._stream_build_logs_enabled()

        try:
            status = subprocess.check_output(["git", "status", "--porcelain"], cwd=str(git_root), env=git_env)
            dirty = bool(status.strip())
        except (subprocess.CalledProcessError, OSError):
            dirty = False
        try:
            current_branch = (
                subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(git_root), env=git_env)
                .decode("utf-8", errors="ignore")
                .strip()
            )
            current_sha = (
                subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(git_root), env=git_env)
                .decode("utf-8", errors="ignore")
                .strip()
            )
            original_ref = current_sha if current_branch == "HEAD" else current_branch
        except (subprocess.CalledProcessError, OSError):
            original_ref = ""
        stashed = False

        log_path_raw = cfg.get("log_path")
        if log_path_raw:
            log_path_expanded = self._expand_value(log_path_raw, ctx)
            if not isinstance(log_path_expanded, str):
                raise TypeError("build_config.log_path must be a string")
            log_path = self._resolve_path(log_path_expanded, base=engine_root)
        else:
            label = f"{art.commit[:8]}_{build_opts.get('tune_tag', 'vanilla')}"
            log_path = engine_root / f"build_{label}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with open(log_path, "a", encoding="utf-8") as logf:
            if dirty:
                logger.info("[build] dirty worktree detected; stashing before build")
                self._run_logged_subprocess(
                    ["git", "stash", "push", "-u", "-m", "shogiarena-build"],
                    cwd=git_root,
                    log_file=logf,
                    env=git_env,
                    stream=stream_build_logs,
                )
                stashed = True
            git_cfg = cfg.get("git") or {}
            checkout_raw = git_cfg.get("checkout") or "{opts.commit}"
            checkout = self._expand_value(checkout_raw, ctx)
            if not isinstance(checkout, str):
                raise TypeError("build_config.git.checkout must be a string")
            self._run_logged_subprocess(
                ["git", "checkout", checkout],
                cwd=git_root,
                log_file=logf,
                env=git_env,
                stream=stream_build_logs,
            )
            self._run_logged_subprocess(
                ["git", "reset", "--hard"],
                cwd=git_root,
                log_file=logf,
                env=git_env,
                stream=stream_build_logs,
            )
            self._run_logged_subprocess(
                ["git", "clean", "-fdx"],
                cwd=git_root,
                log_file=logf,
                env=git_env,
                stream=stream_build_logs,
            )

        applied_tune_patch = False
        backup_plan: list[tuple[Path, Path]] = []
        backup_root: Path | None = None
        tune_file = self._resolve_tune_file(cfg, build_opts, ctx)

        if tune_file:
            tune_path = resolve_path_like(tune_file)
            params_suffix = ".params"
            tune_cfg = cfg.get("tune")
            if isinstance(tune_cfg, Mapping) and tune_cfg.get("params_suffix"):
                params_suffix = str(tune_cfg.get("params_suffix"))
            params_file = str(Path(tune_path).with_suffix(params_suffix))
            logger.info("[SPSA] applying tune after checkout: %s (params: %s)", tune_path, params_file)
            with open(log_path, "a", encoding="utf-8") as logf:
                logf.write(f"[build] tune_file={tune_path}\n")
                logf.write(f"[build] params_file={params_file}\n")
            try:
                tune_blocks = read_tune_file(tune_path)
            except OSError as exc:
                raise RuntimeError(f"Failed to read tune file {tune_path}") from exc
            target_files = sorted(
                {
                    file_name
                    for tb in tune_blocks
                    if (file_name := tb.set_directives.get("file"))
                    if isinstance(file_name, str) and file_name
                }
            )
            if target_files:
                backup_root = engine_root / f".spsa_backup_{art.commit[:8]}"
                for rel in target_files:
                    src_p = (Path(source_dir) / rel).resolve()
                    if not src_p.exists():
                        continue
                    dst_p = (backup_root / rel).resolve()
                    dst_p.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src_p), str(dst_p))
                    backup_plan.append((dst_p, src_p))
            tune_parameters(tune_path, params_file, str(source_dir))
            applied_tune_patch = True

        env_cfg = cfg.get("env") or {}
        if not isinstance(env_cfg, Mapping):
            raise TypeError("build_config.env must be a mapping")
        env_expanded = self._expand_value(env_cfg, ctx)
        if not isinstance(env_expanded, Mapping):
            raise TypeError("build_config.env must resolve to a mapping")
        env = os.environ.copy()
        for key, value in env_expanded.items():
            if value is None:
                continue
            env[str(key)] = str(value)

        commands_cfg = cfg.get("commands")
        if commands_cfg is None:
            raise ValueError("build_config.commands is required")
        if not isinstance(commands_cfg, list):
            raise TypeError("build_config.commands must be a list")
        commands = self._expand_value(commands_cfg, ctx)
        if not isinstance(commands, list):
            raise TypeError("build_config.commands must resolve to a list")
        commands_norm: list[list[str]] = []
        for cmd in commands:
            if not isinstance(cmd, list):
                raise TypeError("build_config.commands entries must be lists")
            if not cmd:
                continue
            commands_norm.append([str(arg) for arg in cmd])

        staged: Path | None = None
        try:
            with open(log_path, "a", encoding="utf-8") as logf:
                for cmd_str in commands_norm:
                    self._run_logged_subprocess(
                        cmd_str,
                        cwd=work_dir,
                        log_file=logf,
                        env=env,
                        stream=stream_build_logs,
                    )

            artifacts_cfg = cfg.get("artifacts")
            if artifacts_cfg is None:
                raise ValueError("build_config.artifacts is required")
            if not isinstance(artifacts_cfg, list):
                raise TypeError("build_config.artifacts must be a list")
            artifacts = self._expand_value(artifacts_cfg, ctx)
            if not isinstance(artifacts, list):
                raise TypeError("build_config.artifacts must resolve to a list")

            for item in artifacts:
                if not isinstance(item, Mapping):
                    raise TypeError("build_config.artifacts entries must be mappings")
                path_raw = item.get("path")
                if not path_raw:
                    continue
                if not isinstance(path_raw, str):
                    raise TypeError("build_config.artifacts.path must be a string")
                candidate = self._resolve_path(path_raw, base=work_dir)
                if not candidate.exists():
                    continue
                chmod = item.get("chmod")
                if chmod:
                    try:
                        os.chmod(candidate, int(str(chmod), 8))
                    except (ValueError, OSError):
                        logger.warning("[build] chmod failed for %s (mode=%s)", candidate, chmod)
                staged = self._stage_artifact(
                    engine_root,
                    art,
                    repo,
                    artifact_id,
                    build_opts,
                    candidate,
                    commands_norm,
                )
                break
            if staged is None:
                raise FileNotFoundError("Built artifact not found after build (check build_config.artifacts)")
        except (subprocess.CalledProcessError, OSError) as exc:
            logger.error("build failed (see build log %s): %s", log_path, exc)
            raise
        finally:
            if applied_tune_patch and backup_plan:
                try:
                    for bak, orig in backup_plan:
                        if not bak.exists():
                            continue
                        orig.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(bak), str(orig))
                except (OSError, shutil.Error) as exc:
                    logger.warning("[SPSA] failed to restore patched file: %s", exc)
                finally:
                    if backup_root and backup_root.exists():
                        shutil.rmtree(str(backup_root), ignore_errors=True)
            with open(log_path, "a", encoding="utf-8") as logf:
                self._run_logged_subprocess(
                    ["git", "reset", "--hard"],
                    cwd=git_root,
                    log_file=logf,
                    env=git_env,
                    stream=stream_build_logs,
                )
                self._run_logged_subprocess(
                    ["git", "clean", "-fdx"],
                    cwd=git_root,
                    log_file=logf,
                    env=git_env,
                    stream=stream_build_logs,
                )
                if original_ref:
                    self._run_logged_subprocess(
                        ["git", "checkout", original_ref],
                        cwd=git_root,
                        log_file=logf,
                        env=git_env,
                        stream=stream_build_logs,
                    )
                if stashed:
                    self._run_logged_subprocess(
                        ["git", "stash", "pop"],
                        cwd=git_root,
                        log_file=logf,
                        env=git_env,
                        stream=stream_build_logs,
                    )
        if staged is None:
            raise FileNotFoundError("Built artifact not found after build (check build_config.artifacts)")
        return staged

    @staticmethod
    def _artifact_hash(
        art: ArtifactId,
        *,
        repo: RepoSettings,
        opts: Mapping[str, Any],
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
        build_opts: Mapping[str, Any],
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
    def _collect_opt_keys(cfg: Mapping[str, Any]) -> set[str]:
        keys: set[str] = set()

        def _walk(value: Any) -> None:
            if isinstance(value, str):
                keys.update(_OPT_PLACEHOLDER_RE.findall(value))
                return
            if isinstance(value, list):
                for item in value:
                    _walk(item)
                return
            if isinstance(value, dict):
                for val in value.values():
                    _walk(val)
                return

        _walk(cfg)
        return keys

    def _select_hash_opts(
        self,
        cfg: Mapping[str, Any],
        build_opts: Mapping[str, Any],
        tune_file: str | None,
    ) -> dict[str, Any]:
        used_keys = self._collect_opt_keys(cfg)
        if tune_file:
            used_keys.add("tune_file")
        if not used_keys:
            return {}
        trimmed: dict[str, Any] = {}
        for key in used_keys:
            if key not in build_opts:
                continue
            value = build_opts[key]
            if isinstance(value, Path):
                trimmed[key] = str(value)
            else:
                trimmed[key] = value
        return trimmed

    @staticmethod
    def _run_logged_subprocess(
        cmd: Sequence[str],
        *,
        cwd: Path,
        log_file: TextIO,
        env: dict[str, str] | None,
        stream: bool,
    ) -> None:
        command_line = f"$ {' '.join(cmd)}\n"
        log_file.write(command_line)
        log_file.flush()
        if stream:
            sys.stdout.write(command_line)
            sys.stdout.flush()
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
                sys.stdout.write(chunk)
                sys.stdout.flush()
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
        except OSError:
            resolved = repo_root
        repo_path = str(resolved)
        key_prefix = "GIT_CONFIG_KEY_"
        val_prefix = "GIT_CONFIG_VALUE_"
        count_raw = env.get("GIT_CONFIG_COUNT", "0")
        try:
            base = int(count_raw)
        except ValueError:
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
