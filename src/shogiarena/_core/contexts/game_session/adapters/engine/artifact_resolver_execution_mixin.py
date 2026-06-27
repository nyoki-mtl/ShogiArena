"""Build execution workflow for artifact resolver."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

from shogiarena._core.contexts.spsa.application.tune import read_tune_file, tune_parameters
from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.paths import resolve_path_like
from shogiarena._core.shared.kernel.settings_loading.settings_models import RepoSettings

from .artifact_resolver_config_mixin import ArtifactResolverConfigMixin
from .artifact_resolver_support_mixin import ArtifactResolverBuildSupportMixin
from .artifact_resolver_types import ArtifactId

logger = logging.getLogger(__name__)


class ArtifactResolverBuildExecutionMixin(ArtifactResolverConfigMixin, ArtifactResolverBuildSupportMixin):
    def _build_from_yaml(
        self,
        engine_root: Path,
        art: ArtifactId,
        build_opts: Mapping[str, JsonValue],
        *,
        repo: RepoSettings,
        artifact_id: str,
        cfg: Mapping[str, JsonValue],
    ) -> Path:
        if not repo.build_config:
            raise ValueError(f"Repo '{repo.name}' has no build_config configured")
        ctx, work_dir, source_dir, git_root = self._build_context(cfg, repo, art, build_opts, engine_root)

        git_env = self._git_env_with_safe_directory(git_root)
        stream_build_logs = self._stream_build_logs_enabled()

        try:
            status = subprocess.check_output(["git", "status", "--porcelain"], cwd=str(git_root), env=git_env)
            dirty = bool(status.strip())
        except (subprocess.CalledProcessError, OSError) as exc:
            raise RuntimeError(f"Failed to inspect git status in {git_root}") from exc
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
        except (subprocess.CalledProcessError, OSError) as exc:
            raise RuntimeError(f"Failed to resolve current git reference in {git_root}") from exc
        is_stashed = False

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
                    should_stream=stream_build_logs,
                )
                is_stashed = True
            git_cfg = self._as_json_mapping(cfg.get("git") or {}, field_name="build_config.git")
            checkout_raw = git_cfg.get("checkout") or "{opts.commit}"
            checkout = self._expand_value(checkout_raw, ctx)
            if not isinstance(checkout, str):
                raise TypeError("build_config.git.checkout must be a string")
            self._run_logged_subprocess(
                ["git", "checkout", checkout],
                cwd=git_root,
                log_file=logf,
                env=git_env,
                should_stream=stream_build_logs,
            )
            self._run_logged_subprocess(
                ["git", "reset", "--hard"],
                cwd=git_root,
                log_file=logf,
                env=git_env,
                should_stream=stream_build_logs,
            )
            self._run_logged_subprocess(
                ["git", "clean", "-fdx"],
                cwd=git_root,
                log_file=logf,
                env=git_env,
                should_stream=stream_build_logs,
            )

        has_applied_tune_patch = False
        backup_plan: list[tuple[Path, Path]] = []
        backup_root: Path | None = None
        tune_file = self._resolve_tune_file(cfg, build_opts, ctx)

        if tune_file:
            tune_path = resolve_path_like(tune_file)
            params_suffix = ".params"
            tune_cfg = cfg.get("tune")
            if isinstance(tune_cfg, Mapping):
                tune_cfg_map = self._as_json_mapping(tune_cfg, field_name="build_config.tune")
                if tune_cfg_map.get("params_suffix"):
                    params_suffix = str(tune_cfg_map.get("params_suffix"))
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
            has_applied_tune_patch = True

        if "env" in cfg:
            raise ValueError("build_config key 'env' is not supported; use 'environment'")
        env_cfg = cfg.get("environment") or {}
        if not isinstance(env_cfg, Mapping):
            raise TypeError("build_config.environment must be a mapping")
        env_expanded = self._expand_value(env_cfg, ctx)
        if not isinstance(env_expanded, Mapping):
            raise TypeError("build_config.environment must resolve to a mapping")
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
                        should_stream=stream_build_logs,
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
                item_map = {str(key): value for key, value in item.items()}
                path_raw = item_map.get("path")
                if not path_raw:
                    continue
                if not isinstance(path_raw, str):
                    raise TypeError("build_config.artifacts.path must be a string")
                candidate = self._resolve_path(path_raw, base=work_dir)
                if not candidate.exists():
                    continue
                chmod = item_map.get("chmod")
                if chmod:
                    try:
                        os.chmod(candidate, int(str(chmod), 8))
                    except (ValueError, OSError):
                        logger.warning("[build] chmod failed for %s (mode=%s)", candidate, chmod, exc_info=True)
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
            if has_applied_tune_patch and backup_plan:
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
                    should_stream=stream_build_logs,
                )
                self._run_logged_subprocess(
                    ["git", "clean", "-fdx"],
                    cwd=git_root,
                    log_file=logf,
                    env=git_env,
                    should_stream=stream_build_logs,
                )
                if original_ref:
                    self._run_logged_subprocess(
                        ["git", "checkout", original_ref],
                        cwd=git_root,
                        log_file=logf,
                        env=git_env,
                        should_stream=stream_build_logs,
                    )
                if is_stashed:
                    self._run_logged_subprocess(
                        ["git", "stash", "pop"],
                        cwd=git_root,
                        log_file=logf,
                        env=git_env,
                        should_stream=stream_build_logs,
                    )
        if staged is None:
            raise FileNotFoundError("Built artifact not found after build (check build_config.artifacts)")
        return staged
