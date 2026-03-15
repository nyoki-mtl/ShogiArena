"""Build-config expansion and option preparation helpers for artifact resolution."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import yaml
from pydantic import ValidationError

from shogiarena._core.platform.host_probe.cpu_detection import detect_target_cpu
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.json_coercion import (
    coerce_json_object as _coerce_json_object_strict,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.paths import resolve_path_like
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str
from shogiarena._core.shared.kernel.settings_loading.settings_models import RepoSettings

from .artifact_resolver_types import (
    ArtifactId,
    _ArtifactBuildConfigModel,
    _BuildTemplateContext,
    _BuildTemplateValue,
)

_OPT_PLACEHOLDER_RE = re.compile(r"\{opts\.([A-Za-z0-9_]+)\}")


class ArtifactResolverConfigMixin:
    @staticmethod
    def _as_json_mapping(value: JsonValue | Mapping[str, JsonValue] | None, *, field_name: str) -> JsonObject:
        return _coerce_json_object_strict(value, field_name=field_name)

    def _load_build_config(self, path: Path) -> JsonObject:
        config_path = path.expanduser()
        if not config_path.exists():
            raise FileNotFoundError(f"build_config not found: {config_path}")
        with open(config_path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        if not isinstance(raw, Mapping):
            raise TypeError(f"build_config must be a mapping: {config_path}")
        try:
            parsed = _ArtifactBuildConfigModel.model_validate(raw)
        except ValidationError as exc:
            raise TypeError(f"build_config schema is invalid: {config_path}: {exc}") from exc
        return _coerce_json_object_strict(parsed.model_dump(mode="python"), field_name="build_config")

    @staticmethod
    def _expand_value(value: JsonValue, ctx: _BuildTemplateContext) -> JsonValue:
        match value:
            case str() as text:
                try:
                    return text.format_map(ctx)
                except (KeyError, AttributeError) as exc:
                    raise ValueError(f"Unknown placeholder in build_config: {text}") from exc
            case list() as items:
                return [ArtifactResolverConfigMixin._expand_value(item, ctx) for item in items]
            case dict() as mapping:
                return {str(key): ArtifactResolverConfigMixin._expand_value(val, ctx) for key, val in mapping.items()}
            case _:
                return value

    @staticmethod
    def _resolve_path(value: str, *, base: Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (base / path).resolve()
        return path

    @staticmethod
    def _config_uses_opt(cfg: Mapping[str, JsonValue], key: str) -> bool:
        needle = f"{{opts.{key}}}"

        def _walk(value: JsonValue | Mapping[str, JsonValue]) -> bool:
            match value:
                case str() as text:
                    return needle in text
                case list() as items:
                    return any(_walk(item) for item in items)
                case dict() as mapping:
                    return any(_walk(val) for val in mapping.values())
                case _:
                    return False

        return _walk(cfg)

    def _prepare_build_opts(
        self,
        cfg: Mapping[str, JsonValue],
        overrides: Mapping[str, JsonValue],
    ) -> JsonObject:
        defaults_raw = cfg.get("defaults") or {}
        defaults = self._as_json_mapping(defaults_raw, field_name="build_config.defaults")
        build_opts: JsonObject = {**defaults, **overrides}

        needs_cpu = "target_cpu" in build_opts or self._config_uses_opt(cfg, "target_cpu")
        if needs_cpu:
            cpu = str(build_opts.get("target_cpu") or "").strip()
            if not cpu:
                try:
                    detected_cpu = detect_target_cpu()
                    cpu = str(detected_cpu).strip()
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
            tune_tag = coerce_str(raw_tag) or "vanilla"
            tune_tag = re.sub(r"[^A-Za-z0-9._-]", "-", tune_tag)
            build_opts["tune_tag"] = tune_tag

        return build_opts

    def _build_context(
        self,
        cfg: Mapping[str, JsonValue],
        repo: RepoSettings,
        art: ArtifactId,
        build_opts: Mapping[str, JsonValue],
        engine_root: Path,
    ) -> tuple[dict[str, _BuildTemplateValue], Path, Path, Path]:
        repo_ctx = {
            "path": str(repo.path),
            "name": repo.name,
            "url": repo.url or "",
        }
        opts_ctx: JsonObject = {str(key): value for key, value in build_opts.items()}
        opts_ctx.setdefault("commit", art.commit)
        opts_ctx.setdefault("engine", art.engine)
        paths_ctx = {
            "engine_root": str(engine_root),
            "engine_dir": str(project_dirs.engine_dir),
            "output_dir": str(project_dirs.output_dir),
        }
        ctx: dict[str, _BuildTemplateValue] = {
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

        git_cfg = self._as_json_mapping(cfg.get("git") or {}, field_name="build_config.git")
        git_root_raw = git_cfg.get("root") or str(source_dir)
        git_root_expanded = self._expand_value(git_root_raw, ctx)
        if not isinstance(git_root_expanded, str):
            raise TypeError("build_config.git.root must be a string")
        git_root = self._resolve_path(git_root_expanded, base=source_dir)

        return ctx, work_dir, source_dir, git_root

    def _resolve_tune_file(
        self,
        cfg: Mapping[str, JsonValue],
        build_opts: Mapping[str, JsonValue],
        ctx: _BuildTemplateContext,
    ) -> str | None:
        tune_cfg = cfg.get("tune")
        is_tune_enabled = True
        tune_cfg_map: JsonObject | None = None
        if tune_cfg is False:
            is_tune_enabled = False
        if isinstance(tune_cfg, Mapping):
            tune_cfg_map = self._as_json_mapping(tune_cfg, field_name="build_config.tune")
            is_tune_enabled = bool(tune_cfg_map.get("enabled", True))
        if not is_tune_enabled:
            return None

        if tune_cfg_map is not None and tune_cfg_map.get("file"):
            tune_file_expanded = self._expand_value(tune_cfg_map.get("file"), ctx)
            if not isinstance(tune_file_expanded, str):
                raise TypeError("build_config.tune.file must be a string")
            return tune_file_expanded

        tune_file = build_opts.get("tune_file")
        if tf_str := coerce_str(tune_file):
            return tf_str
        return None

    @staticmethod
    def _tune_file_sha256(tune_file: str | None) -> str:
        if not tune_file:
            return ""
        tune_path = Path(resolve_path_like(tune_file))
        if tune_path.exists():
            digest = hashlib.sha256()
            with open(tune_path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        return ""

    @staticmethod
    def _collect_opt_keys(cfg: Mapping[str, JsonValue]) -> set[str]:
        keys: set[str] = set()

        def _walk(value: JsonValue | Mapping[str, JsonValue]) -> None:
            match value:
                case str() as text:
                    keys.update(_OPT_PLACEHOLDER_RE.findall(text))
                case list() as items:
                    for item in items:
                        _walk(item)
                case dict() as mapping:
                    for item in mapping.values():
                        _walk(item)

        _walk(cfg)
        return keys

    def _select_hash_opts(
        self,
        cfg: Mapping[str, JsonValue],
        build_opts: Mapping[str, JsonValue],
        tune_file: str | None,
    ) -> JsonObject:
        used_keys = self._collect_opt_keys(cfg)
        if tune_file:
            used_keys.add("tune_file")
        if not used_keys:
            return {}
        trimmed: JsonObject = {}
        for key in used_keys:
            if key in build_opts:
                trimmed[key] = build_opts[key]
        return trimmed
