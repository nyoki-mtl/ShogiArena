"""Engine runtime factory contracts for instance orchestration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.service_ports import ArtifactResolutionPort


class EngineFactoryPort(Protocol):
    """Protocol for engine runtime creation implementations."""

    async def create_engine(
        self,
        config_path: Path,
        timeout: float = 10.0,
        extra_options: JsonObject | None = None,
        engine_name: str | None = None,
        instance_id: str | None = None,
        instance_pool: Any = None,
        cpu_affinity: Sequence[int] | None = None,
        *,
        artifact_resolver: ArtifactResolutionPort | None = None,
    ) -> Any: ...

    async def create_engine_from_mapping(
        self,
        config_mapping: Mapping[str, object],
        timeout: float = 10.0,
        extra_options: JsonObject | None = None,
        engine_name: str | None = None,
        instance_id: str | None = None,
        instance_pool: Any = None,
        cpu_affinity: Sequence[int] | None = None,
        *,
        artifact_resolver: ArtifactResolutionPort | None = None,
    ) -> Any: ...

    async def _rewrite_options_for_remote(self, instance: Any, options: JsonObject) -> None: ...

    async def _compute_exec_paths(self, instance: Any, local_resolved: str) -> tuple[str, str]: ...


@dataclass(frozen=True, slots=True)
class EngineFactoryService:
    """Injectable engine factory replacing the former static EngineFactory facade.

    すべての engine 生成・リモートオプション書換を担当する。
    composition root で構築し、必要な consumer に注入する。
    """

    factory: EngineFactoryPort
    artifact_resolver: ArtifactResolutionPort | None = None

    def _select_artifact_resolver(
        self,
        resolver: ArtifactResolutionPort | None,
    ) -> ArtifactResolutionPort | None:
        return resolver or self.artifact_resolver

    async def create_engine(
        self,
        config_path: Path,
        timeout: float = 10.0,
        extra_options: JsonObject | None = None,
        engine_name: str | None = None,
        instance_id: str | None = None,
        instance_pool: Any = None,
        cpu_affinity: Sequence[int] | None = None,
        *,
        artifact_resolver: ArtifactResolutionPort | None = None,
    ) -> Any:
        """エンジン設定ファイルからエンジンランタイムを生成する。"""
        return await self.factory.create_engine(
            config_path,
            timeout=timeout,
            extra_options=extra_options,
            engine_name=engine_name,
            instance_id=instance_id,
            instance_pool=instance_pool,
            cpu_affinity=cpu_affinity,
            artifact_resolver=self._select_artifact_resolver(artifact_resolver),
        )

    async def create_engine_from_mapping(
        self,
        config_mapping: Mapping[str, object],
        timeout: float = 10.0,
        extra_options: JsonObject | None = None,
        engine_name: str | None = None,
        instance_id: str | None = None,
        instance_pool: Any = None,
        cpu_affinity: Sequence[int] | None = None,
        *,
        artifact_resolver: ArtifactResolutionPort | None = None,
    ) -> Any:
        """マッピングペイロードからエンジンランタイムを生成する。"""
        return await self.factory.create_engine_from_mapping(
            config_mapping,
            timeout=timeout,
            extra_options=extra_options,
            engine_name=engine_name,
            instance_id=instance_id,
            instance_pool=instance_pool,
            cpu_affinity=cpu_affinity,
            artifact_resolver=self._select_artifact_resolver(artifact_resolver),
        )

    async def rewrite_options_for_remote(self, instance: Any, options: JsonObject) -> None:
        """リモートインスタンス用にオプションパスを書き換える。"""
        await self.factory._rewrite_options_for_remote(instance, options)


__all__ = [
    "EngineFactoryService",
]
