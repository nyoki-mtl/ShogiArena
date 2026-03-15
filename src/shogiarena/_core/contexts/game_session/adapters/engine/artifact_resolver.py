"""Artifact resolution and build orchestration for engine binaries."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.json_coercion import (
    coerce_json_value as _coerce_json_value_strict,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.settings_loading.settings_models import RepoSettings

from .artifact_resolver_execution_mixin import ArtifactResolverBuildExecutionMixin
from .artifact_resolver_types import ArtifactId

logger = logging.getLogger(__name__)


class ArtifactResolver(
    ArtifactResolverBuildExecutionMixin,
):
    """Resolve engine artifacts to local binary paths, building on demand."""

    def resolve(self, artifact: str, overrides: Mapping[str, JsonValue] | None = None) -> Path:
        """Return local binary path for ``artifact``, building if missing."""

        art = ArtifactId.parse(artifact)
        overrides_map: JsonObject = {
            str(key): _coerce_json_value_strict(value, field_name=f"build_options.{key}")
            for key, value in (overrides or {}).items()
        }

        repo_obj = project_dirs.repos.get(art.engine)
        if not isinstance(repo_obj, RepoSettings):
            raise ValueError(
                f"Unknown repo '{art.engine}' (configure it in settings.yaml). "
                "Run `shogiarena config init` to create settings.yaml, "
                "then use `shogiarena config repo set` to add repositories."
            )
        repo = repo_obj
        if not repo.build_config:
            raise ValueError(f"Repo '{art.engine}' has no build_config configured in settings.yaml")
        if not repo.build_config.exists():
            raise FileNotFoundError(f"build_config not found: {repo.build_config}")
        engine_root = project_dirs.engine_dir / art.engine
        engine_root.mkdir(parents=True, exist_ok=True)

        cfg = self._load_build_config(repo.build_config)
        build_config_sha256 = self._file_sha256(repo.build_config)
        build_opts = self._prepare_build_opts(cfg, overrides_map)
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


__all__ = ["ArtifactId", "ArtifactResolver"]
