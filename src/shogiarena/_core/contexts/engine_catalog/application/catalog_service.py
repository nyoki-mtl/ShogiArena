from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from shogiarena._core.contexts.engine_catalog.domain.instance_defaults import compute_instance_defaults
from shogiarena._core.contexts.engine_catalog.ports.catalog_service import (
    EngineCatalogEntryRequest,
    EngineCatalogPort,
    EngineCatalogRequest,
)
from shogiarena._core.contexts.engine_catalog.ports.file_loader import EngineCatalogFileLoaderPort
from shogiarena._core.shared.kernel.book_provenance import build_book_provenance
from shogiarena._core.shared.kernel.json_coercion import to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.overlay_options import select_overlay_options
from shogiarena._core.shared.kernel.paths import maybe_resolve_path_option, resolve_path_like
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.service_ports import ArtifactResolutionPort
from shogiarena._core.shared.kernel.time_control_resolution import compute_time_control_specs

logger = logging.getLogger(__name__)


class EngineCatalogService(EngineCatalogPort):
    def __init__(
        self,
        *,
        file_loader: EngineCatalogFileLoaderPort,
        artifact_resolver: ArtifactResolutionPort | None = None,
    ) -> None:
        self._file_loader = file_loader
        self._artifact_resolver = artifact_resolver

    def build_metadata(self, request: EngineCatalogRequest) -> list[JsonObject]:
        metadata: list[JsonObject] = []
        seen: set[str] = set()
        for entry in request.entries:
            if entry.name in seen:
                continue
            seen.add(entry.name)
            metadata.append(self._build_entry_metadata(request=request, entry=entry))
        return metadata

    def compute_time_control_specs(self, request: EngineCatalogRequest) -> tuple[dict[str, str], str | None]:
        return compute_time_control_specs(
            base_time_control=request.base_time_control,
            entries=((entry.name, entry.time_control) for entry in request.entries),
        )

    def compute_instance_defaults(self, request: EngineCatalogRequest) -> dict[str, str | None]:
        return compute_instance_defaults((entry.name, entry.instance_id) for entry in request.entries)

    def _build_entry_metadata(self, *, request: EngineCatalogRequest, entry: EngineCatalogEntryRequest) -> JsonObject:
        metadata: JsonObject = {
            "name": entry.name,
            "engine_config_path": str(entry.engine_config_path) if entry.engine_config_path is not None else None,
        }

        config_mapping = self._load_config_mapping(entry.engine_config_path)
        config_options = self._extract_options_mapping(
            config_mapping.get("options") if config_mapping is not None else None,
            path=entry.engine_config_path,
            field_name="options",
        )
        if config_options:
            metadata["config_options"] = dict(config_options)

        artifact_overlay_options = self._load_overlay_options(entry.artifact_overlay_path)
        overlay_options = self._load_entry_overlay_options(entry.options_overlay_paths)
        all_overlay_options = dict(artifact_overlay_options)
        all_overlay_options.update(overlay_options)
        if all_overlay_options:
            metadata["overlay_options"] = dict(all_overlay_options)

        inline_options = dict(entry.inline_options)
        if inline_options:
            metadata["extra_options"] = dict(inline_options)

        resolved_engine_path = self._resolve_engine_path(request=request, entry=entry, config_mapping=config_mapping)
        if resolved_engine_path is not None:
            metadata["engine_path"] = resolved_engine_path

        merged_options = dict(config_options)
        option_sources: dict[str, str] = {}
        option_source_details: dict[str, str] = {}
        self._apply_option_source(
            merged_options=merged_options,
            option_sources=option_sources,
            option_source_details=option_source_details,
            options=config_options,
            source_name="config",
            detail=self._format_source("engine config", entry.engine_config_path),
        )
        self._apply_option_source(
            merged_options=merged_options,
            option_sources=option_sources,
            option_source_details=option_source_details,
            options=artifact_overlay_options,
            source_name="overlay",
            detail=self._format_source("artifact overlay", entry.artifact_overlay_path),
        )
        self._apply_option_source(
            merged_options=merged_options,
            option_sources=option_sources,
            option_source_details=option_source_details,
            options=request.rules_synced_options,
            source_name="rules",
            detail=self._format_source("rules.adjudication", request.config_source_path),
        )
        for overlay_path in entry.options_overlay_paths:
            self._apply_option_source(
                merged_options=merged_options,
                option_sources=option_sources,
                option_source_details=option_source_details,
                options=self._load_overlay_options(overlay_path),
                source_name="overlay",
                detail=self._format_source("options overlays", overlay_path),
            )
        self._apply_option_source(
            merged_options=merged_options,
            option_sources=option_sources,
            option_source_details=option_source_details,
            options=inline_options,
            source_name="override",
            detail=self._format_source("engines[].options", request.config_source_path),
        )

        if merged_options:
            metadata["merged_options"] = dict(merged_options)
            metadata["option_sources"] = option_sources
            metadata["option_sources_details"] = option_source_details
            metadata["resolved_options"] = self._resolve_path_options(
                merged_options,
                run_dir=request.run_dir,
                request_engine_dir=request.engine_dir,
                name=entry.name,
            )
            # 内蔵定跡(A) の provenance サマリ（解決後 path / 軽量 fingerprint / 主要 option）。Task 0015。
            book_provenance = build_book_provenance(
                merged_options,
                output_dir=request.run_dir,
                engine_dir=request.engine_dir,
            )
            if book_provenance is not None:
                metadata["book"] = book_provenance

        runtime_options = request.runtime_options.get(entry.name)
        if runtime_options:
            metadata["runtime_usi_options"] = to_json_object(runtime_options)
        runtime_info = request.runtime_info.get(entry.name)
        if runtime_info:
            metadata["runtime_engine_info"] = dict(runtime_info)
        return metadata

    def _resolve_engine_path(
        self,
        *,
        request: EngineCatalogRequest,
        entry: EngineCatalogEntryRequest,
        config_mapping: Mapping[str, object] | None,
    ) -> str | None:
        if config_mapping is not None:
            raw_engine_path = coerce_str(config_mapping.get("engine_path"))
            if raw_engine_path is not None:
                return resolve_path_like(
                    raw_engine_path,
                    output_dir=request.path_output_dir,
                    engine_dir=request.engine_dir,
                )
            raw_artifact = coerce_str(config_mapping.get("artifact"))
            if raw_artifact is not None:
                build_options = self._extract_options_mapping(
                    config_mapping.get("build_options"),
                    path=entry.engine_config_path,
                    field_name="build_options",
                )
                return self._resolve_artifact(raw_artifact, build_options)
            if config_mapping.get("engine_path") is not None or config_mapping.get("artifact") is not None:
                raise ValueError(
                    f"Invalid engine config {entry.engine_config_path}: specify a non-empty engine_path or artifact"
                )

        if entry.artifact is not None and entry.artifact.strip():
            return self._resolve_artifact(entry.artifact, entry.build_options)
        return None

    def _resolve_artifact(self, artifact: str, build_options: JsonObject) -> str:
        resolver = self._artifact_resolver
        if resolver is None:
            raise RuntimeError("artifact_resolver is required to resolve artifact-backed engine metadata")
        return str(resolver(artifact, build_options))

    def _resolve_path_options(
        self,
        options: JsonObject,
        *,
        run_dir: Path,
        request_engine_dir: Path,
        name: str,
    ) -> JsonObject:
        resolved_options: JsonObject = {}
        for key, value in options.items():
            try:
                resolved_value = maybe_resolve_path_option(
                    key,
                    value,
                    output_dir=run_dir,
                    engine_dir=request_engine_dir,
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.debug("Failed to resolve path option %s for engine %s: %s", key, name, exc, exc_info=True)
                resolved_value = value
            resolved_options[key] = json_serialize(resolved_value)
        return resolved_options

    def _load_config_mapping(self, path: Path | None) -> Mapping[str, object] | None:
        if path is None:
            return None
        return self._file_loader.load_yaml_mapping(path)

    def _load_entry_overlay_options(self, overlay_paths: tuple[Path, ...]) -> JsonObject:
        merged: JsonObject = {}
        for overlay_path in overlay_paths:
            merged.update(self._load_overlay_options(overlay_path))
        return merged

    def _load_overlay_options(self, path: Path | None) -> JsonObject:
        if path is None:
            return {}
        payload = self._file_loader.load_yaml_mapping(path)
        overlay_options = select_overlay_options(payload, source=str(path))
        return self._extract_options_mapping(overlay_options, path=path, field_name="options")

    def _extract_options_mapping(
        self,
        raw: object,
        *,
        path: Path | None,
        field_name: str,
    ) -> JsonObject:
        if raw is None:
            return {}
        if not isinstance(raw, Mapping):
            path_label = f" in {path}" if path is not None else ""
            raise TypeError(f"{field_name} must be a mapping{path_label}")
        normalized_raw = {str(key): value for key, value in raw.items()}
        return to_json_object(normalized_raw)

    def _apply_option_source(
        self,
        *,
        merged_options: JsonObject,
        option_sources: dict[str, str],
        option_source_details: dict[str, str],
        options: JsonObject,
        source_name: str,
        detail: str | None,
    ) -> None:
        for key, value in options.items():
            merged_options[key] = json_serialize(value)
            option_sources[key] = source_name
            if detail is not None:
                option_source_details[key] = detail
            else:
                option_source_details.pop(key, None)

    def _format_source(self, label: str, candidate: Path | str | None) -> str | None:
        if candidate is None:
            return None
        try:
            path_obj = Path(str(candidate)).expanduser().resolve(strict=False)
            return f"{label} ({path_obj})"
        except (OSError, RuntimeError, ValueError):
            return f"{label} ({candidate})"


__all__ = ["EngineCatalogService"]
