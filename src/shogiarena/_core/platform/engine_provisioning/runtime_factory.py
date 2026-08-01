"""Platform-level engine runtime factory implementation."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, TypeAlias, cast

from shogiarena._core.platform.engine_provisioning.provisioning_ports import EngineRuntimeInstancePort
from shogiarena._core.platform.engine_provisioning.spawner_backed_usi_bridge import SpawnerBackedUSIBridge
from shogiarena._core.platform.host_probe.cpu_detection import detect_target_cpu
from shogiarena._core.platform.settings import project_dirs
from shogiarena._core.shared.kernel.engine_book import (
    resolve_engine_book_path,
    resolve_yaneuraou_book_fallback_path,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.paths import PATH_OPTION_KEYS, resolve_path_like
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.service_ports import ArtifactResolutionPort

logger = logging.getLogger(__name__)

# 内蔵定跡(A) の remote 転送ポリシー（Task 0017）。
# 既定では大型 book の意図しない自動転送を防ぐため、閾値超は hard block（明示 opt-in が必要）。
#   env SHOGIARENA_REMOTE_BOOK_TRANSFER: "auto"(既定) | "always" | "preplaced"
#     - auto:      閾値以下のみ自動転送。閾値超は明確なエラーで停止。
#     - always:    サイズに関わらず content-hash 転送（ensure-once で初回のみ）。
#     - preplaced: 転送せず、worker 側の同一パスを参照（fingerprint は provenance に記録）。
#   env SHOGIARENA_REMOTE_BOOK_MAX_MB: 自動転送の上限（MiB, 既定 256）。
_DEFAULT_REMOTE_BOOK_MAX_MB = 256
_REMOTE_BOOK_TRANSFER_ENV = "SHOGIARENA_REMOTE_BOOK_TRANSFER"
_REMOTE_BOOK_MAX_MB_ENV = "SHOGIARENA_REMOTE_BOOK_MAX_MB"
_REMOTE_BOOK_PREPLACED_PATH_ENV = "SHOGIARENA_REMOTE_BOOK_PREPLACED_PATH"
_REMOTE_BOOK_PREPLACED_SHA256_ENV = "SHOGIARENA_REMOTE_BOOK_PREPLACED_SHA256"

BookTransferMode = Literal["auto", "always", "preplaced"]
UsiOptionValidationMode: TypeAlias = Literal["strict", "warn", "raw", "allow_unlisted_combo_value"]


@dataclass(frozen=True, slots=True)
class _BookRemotePolicy:
    mode: BookTransferMode
    max_bytes: int
    preplaced_path: str | None
    preplaced_sha256: str | None


def _resolve_book_remote_policy() -> _BookRemotePolicy:
    raw_mode = os.environ.get(_REMOTE_BOOK_TRANSFER_ENV, "auto").strip().lower()
    mode: BookTransferMode
    if raw_mode == "always":
        mode = "always"
    elif raw_mode == "preplaced":
        mode = "preplaced"
    else:
        mode = "auto"
        if raw_mode not in ("auto", ""):
            logger.warning("Unknown %s=%r; falling back to 'auto'", _REMOTE_BOOK_TRANSFER_ENV, raw_mode)
    raw_mb = os.environ.get(_REMOTE_BOOK_MAX_MB_ENV)
    max_mb = _DEFAULT_REMOTE_BOOK_MAX_MB
    if raw_mb is not None:
        try:
            max_mb = max(0, int(raw_mb))
        except ValueError:
            logger.warning("Invalid %s=%r; using default %d MiB", _REMOTE_BOOK_MAX_MB_ENV, raw_mb, max_mb)
    preplaced_path = os.environ.get(_REMOTE_BOOK_PREPLACED_PATH_ENV)
    preplaced_sha256 = os.environ.get(_REMOTE_BOOK_PREPLACED_SHA256_ENV)
    if mode == "preplaced":
        if not preplaced_path or not preplaced_sha256:
            raise ValueError(
                "preplaced book requires both "
                f"{_REMOTE_BOOK_PREPLACED_PATH_ENV} and {_REMOTE_BOOK_PREPLACED_SHA256_ENV}"
            )
        if "\\" in preplaced_path or not PurePosixPath(preplaced_path).is_absolute():
            raise ValueError(f"{_REMOTE_BOOK_PREPLACED_PATH_ENV} must be an absolute POSIX path")
        if re.fullmatch(r"[0-9a-f]{64}", preplaced_sha256) is None:
            raise ValueError(f"{_REMOTE_BOOK_PREPLACED_SHA256_ENV} must be a lowercase SHA-256 digest")
    return _BookRemotePolicy(
        mode=mode,
        max_bytes=max_mb * 1024 * 1024,
        preplaced_path=preplaced_path,
        preplaced_sha256=preplaced_sha256,
    )


class _InstancePoolPort(Protocol):
    def get_instance(self, instance_id: str) -> EngineRuntimeInstancePort | None: ...

    def ensure_local_instance(self) -> EngineRuntimeInstancePort: ...


class _EngineRuntimeSupportPort(Protocol):
    def detect_remote_target_cpu(self, instance: object) -> str | Awaitable[str]: ...

    async def ensure_remote_binary(self, instance: object, local_binary: Path, remote_binary: str) -> None: ...

    def build_local_manifest(self, path: Path) -> Mapping[str, object]: ...

    def file_sha256(self, path: Path) -> str: ...

    async def ensure_remote_dir_by_manifest(self, instance: object, local_dir: Path, remote_dir: str) -> None: ...

    async def ensure_remote_file(self, instance: object, local_file: Path, remote_file: str) -> None: ...

    async def verify_remote_file_sha256(
        self,
        instance: object,
        remote_file: str,
        expected_sha256: str,
    ) -> bool: ...


class _BinaryResolutionConfigPort(Protocol):
    artifact: str | None
    build_options: Mapping[str, JsonValue]


_EngineConfigFactory = Callable[[Path], Any]
_EngineMappingFactory = Callable[[Mapping[str, JsonValue]], Any]
_EngineSessionFactory = Callable[..., Any]


class EngineRuntimeFactory:
    """Concrete implementation of engine runtime creation for shared contexts."""

    _ensured_remote_dirs: set[str] = set()
    _ensure_dir_locks: dict[str, asyncio.Lock] = {}
    _ensure_file_locks: dict[str, asyncio.Lock] = {}
    _ensure_binary_locks: dict[str, asyncio.Lock] = {}

    def __init__(
        self,
        *,
        process_spawner: Callable[..., Awaitable[Any]],
        support: _EngineRuntimeSupportPort,
        engine_config_factory: _EngineConfigFactory,
        mapping_config_factory: _EngineMappingFactory,
        engine_session_factory: _EngineSessionFactory,
    ) -> None:
        self._process_spawner = process_spawner
        self._support = support
        self._engine_config_factory = engine_config_factory
        self._mapping_config_factory = mapping_config_factory
        self._engine_session_factory = engine_session_factory

    async def create_engine(
        self,
        config_path: Path,
        timeout: float = 10.0,
        extra_options: JsonObject | None = None,
        go_options: JsonObject | None = None,
        engine_name: str | None = None,
        instance_id: str | None = None,
        instance_pool: _InstancePoolPort | None = None,
        cpu_affinity: Sequence[int] | None = None,
        collect_info_strings: bool | None = None,
        collect_raw_io: bool | None = None,
        collect_stderr: bool | None = None,
        collect_outbound: bool | None = None,
        option_validation: UsiOptionValidationMode | None = None,
        *,
        artifact_resolver: ArtifactResolutionPort | None = None,
    ) -> Any:
        config = self._engine_config_factory(config_path)
        config = self._apply_overrides(
            config,
            extra_options=extra_options,
            go_options=go_options,
            collect_info_strings=collect_info_strings,
            collect_raw_io=collect_raw_io,
            collect_stderr=collect_stderr,
            collect_outbound=collect_outbound,
            option_validation=option_validation,
        )
        return await self._create_engine_from_config(
            config,
            timeout=timeout,
            engine_name=engine_name,
            instance_id=instance_id,
            instance_pool=instance_pool,
            cpu_affinity=cpu_affinity,
            artifact_resolver=artifact_resolver,
        )

    async def create_engine_from_mapping(
        self,
        config_mapping: Mapping[str, object],
        timeout: float = 10.0,
        extra_options: JsonObject | None = None,
        go_options: JsonObject | None = None,
        engine_name: str | None = None,
        instance_id: str | None = None,
        instance_pool: _InstancePoolPort | None = None,
        cpu_affinity: Sequence[int] | None = None,
        collect_info_strings: bool | None = None,
        collect_raw_io: bool | None = None,
        collect_stderr: bool | None = None,
        collect_outbound: bool | None = None,
        option_validation: UsiOptionValidationMode | None = None,
        *,
        artifact_resolver: ArtifactResolutionPort | None = None,
    ) -> Any:
        # 呼び出し側（adapters 層）が json_serialize 済みのペイロードを渡す契約のため、
        # ここでは実行時変換を行わず JSON 値として扱う。
        config = self._mapping_config_factory(cast("Mapping[str, JsonValue]", config_mapping))
        config = self._apply_overrides(
            config,
            extra_options=extra_options,
            go_options=go_options,
            collect_info_strings=collect_info_strings,
            collect_raw_io=collect_raw_io,
            collect_stderr=collect_stderr,
            collect_outbound=collect_outbound,
            option_validation=option_validation,
        )
        return await self._create_engine_from_config(
            config,
            timeout=timeout,
            engine_name=engine_name,
            instance_id=instance_id,
            instance_pool=instance_pool,
            cpu_affinity=cpu_affinity,
            artifact_resolver=artifact_resolver,
        )

    async def _create_engine_from_config(
        self,
        config: Any,
        *,
        timeout: float,
        engine_name: str | None,
        instance_id: str | None,
        instance_pool: _InstancePoolPort | None,
        cpu_affinity: Sequence[int] | None,
        artifact_resolver: ArtifactResolutionPort | None,
    ) -> Any:
        instance = self._determine_instance(instance_id, instance_pool)

        local_engine_path = config.engine_path
        if not local_engine_path:
            local_engine_path = await self._resolve_engine_binary(
                config,
                instance_id=instance_id,
                instance_pool=instance_pool,
                artifact_resolver=artifact_resolver,
            )
            config = config.with_overrides(engine_path=local_engine_path)

        working_dir_candidate = config.working_directory or str(Path(local_engine_path).parent)
        engine_exec_path, working_dir_exec = await self._compute_exec_paths(instance, local_engine_path)

        options_for_runtime = {str(key): value for key, value in config.options.items()}
        if instance.is_ssh and options_for_runtime:
            await self._rewrite_options_for_remote(instance, options_for_runtime)

        config = config.with_overrides(
            name=engine_name or config.name,
            engine_path=engine_exec_path,
            working_directory=working_dir_exec or working_dir_candidate,
            options=options_for_runtime,
        )

        bridge = SpawnerBackedUSIBridge(
            instance=instance,
            engine_path=engine_exec_path,
            working_dir=working_dir_exec or working_dir_candidate,
            name=config.name,
            engine_args=list(config.engine_args),
            env=dict(config.environment),
            cpu_affinity=tuple(cpu_affinity) if cpu_affinity else None,
            engine_spawner=self._process_spawner,
        )

        handshake_timeout = config.handshake_timeout if config.handshake_timeout is not None else timeout
        return self._engine_session_factory(
            config=config,
            bridge=bridge,
            handshake_timeout=handshake_timeout,
            should_collect_info_strings=config.should_collect_info_strings,
            should_collect_raw_io=config.should_collect_raw_io,
            should_collect_stderr=config.should_collect_stderr,
            should_collect_outbound=config.should_collect_outbound,
        )

    async def _rewrite_options_for_remote(self, instance: EngineRuntimeInstancePort, options: JsonObject) -> None:
        engine_dir = Path(instance.config.engine_dir)
        if engine_dir.name != "engines" or engine_dir.parent.name != "data":
            raise ValueError(f"Invalid instance.config.engine_dir; expected .../data/engines. Got: {engine_dir}")
        remote_project_root = engine_dir.parent.parent
        remote_eval_root = remote_project_root / "data" / "evals"

        # 内蔵定跡(A) は BookDir 丸ごとではなく、解決済みの book FILE 単体を
        # content-hash 名で転送する（Task 0017）。ensure_remote_file は同一 hash なら
        # 再転送しないため、2.4GB 級でも初回のみ転送され以降は worker 上で再利用される
        # （事前配置相当）。BookFile が絶対のときは BookDir を無視する YaneuraOu 挙動に
        # 合わせ、composite 表現（BookDir + 相対 BookFile）を維持して書き換える。
        handled: set[str] = set()
        await self._rewrite_book_for_remote(instance, options, remote_eval_root, handled)

        for key, value in list(options.items()):
            if key in handled:
                continue
            if not isinstance(value, str):
                continue
            if key not in PATH_OPTION_KEYS:
                if ("/" in value) or ("\\" in value):
                    logger.warning(
                        "USI option '%s' value contains a path-like separator but key is not path-typed; leaving as-is",
                        key,
                    )
                continue

            resolved = Path(resolve_path_like(value)).resolve()
            if not resolved.exists():
                raise FileNotFoundError(f"Extra option '{key}' references missing path: {resolved}")
            if resolved.is_dir():
                local_manifest = self._support.build_local_manifest(resolved)
                digest = str(local_manifest.get("digest", ""))
                short = digest[:8] if digest else "unknown"
                remote_dir_path = remote_eval_root / f"{resolved.name}-{short}"
                remote_dir = str(remote_dir_path)
                lock = self._ensure_dir_locks.setdefault(remote_dir, asyncio.Lock())
                async with lock:
                    if remote_dir not in self._ensured_remote_dirs:
                        await self._support.ensure_remote_dir_by_manifest(instance, resolved, remote_dir)
                        self._ensured_remote_dirs.add(remote_dir)
                    else:
                        logger.debug("[remote-options] skip ensure (cached this run): %s", remote_dir)
                options[key] = remote_dir
            elif resolved.is_file():
                remote_file = await self._ensure_remote_file_by_hash(instance, resolved, remote_eval_root)
                options[key] = remote_file

    async def _rewrite_book_for_remote(
        self,
        instance: EngineRuntimeInstancePort,
        options: JsonObject,
        remote_eval_root: Path,
        handled: set[str],
    ) -> None:
        """内蔵定跡(A) の book FILE を content-hash 名で転送し、composite option を書き換える。

        内蔵定跡が無効、または book file が絶対パスの実ファイルとして解決できない場合は何もしない
        （その場合は汎用 path option 経路で従来どおり扱う）。
        """

        resolved_book = resolve_engine_book_path(options)
        if resolved_book is None:
            return
        book_path = resolve_yaneuraou_book_fallback_path(Path(resolved_book))
        if not book_path.is_absolute() or not book_path.is_file():
            return

        policy = _resolve_book_remote_policy()
        size = book_path.stat().st_size

        if policy.mode == "preplaced":
            assert policy.preplaced_path is not None
            assert policy.preplaced_sha256 is not None
            local_digest = self._support.file_sha256(book_path)
            if local_digest != policy.preplaced_sha256:
                raise ValueError(
                    f"preplaced book digest does not match local resource: "
                    f"expected {policy.preplaced_sha256}, got {local_digest}"
                )
            if not await self._support.verify_remote_file_sha256(
                instance,
                policy.preplaced_path,
                policy.preplaced_sha256,
            ):
                raise ValueError(
                    f"preplaced book does not exist or failed SHA-256 verification: {policy.preplaced_path}"
                )
            remote_book = PurePosixPath(policy.preplaced_path)
            logger.info(
                "Opening book verified as preplaced on remote worker: %s (%.1f MiB)",
                remote_book,
                size / (1024 * 1024),
            )
            options["BookDir"] = str(remote_book.parent)
            options["BookFile"] = remote_book.name
            handled.add("BookDir")
            handled.add("BookFile")
            return

        if policy.mode == "auto" and size > policy.max_bytes:
            raise ValueError(
                f"Opening book is too large for automatic remote transfer "
                f"({size / (1024 * 1024):.1f} MiB > {policy.max_bytes / (1024 * 1024):.0f} MiB): {book_path}. "
                f"Set {_REMOTE_BOOK_TRANSFER_ENV}=always to opt into transfer, or "
                f"{_REMOTE_BOOK_TRANSFER_ENV}=preplaced to reference a book already present on the worker, "
                f"or raise {_REMOTE_BOOK_MAX_MB_ENV}."
            )

        if size > policy.max_bytes:
            logger.warning(
                "Transferring a large opening book (%.1f MiB) to remote worker on first use: %s "
                "(content-hashed; reused on subsequent runs)",
                size / (1024 * 1024),
                book_path,
            )

        remote_file = await self._ensure_remote_file_by_hash(instance, book_path, remote_eval_root)
        remote_path = Path(remote_file)
        # composite 表現を維持: BookDir=remote dir, BookFile=basename（Combine で remote_file に解決）。
        options["BookDir"] = str(remote_path.parent)
        options["BookFile"] = remote_path.name
        handled.add("BookDir")
        handled.add("BookFile")

    async def _ensure_remote_file_by_hash(
        self,
        instance: EngineRuntimeInstancePort,
        local_file: Path,
        remote_eval_root: Path,
    ) -> str:
        """ローカルファイルを content-hash 付き名で remote へ配置し、remote パスを返す。"""

        local_hash = self._support.file_sha256(local_file)
        short = local_hash[:8]
        remote_file = str(remote_eval_root / f"{local_file.stem}-{short}{local_file.suffix}")
        file_lock = self._ensure_file_locks.setdefault(remote_file, asyncio.Lock())
        async with file_lock:
            await self._support.ensure_remote_file(instance, local_file, remote_file)
        return remote_file

    async def _resolve_engine_binary(
        self,
        config: _BinaryResolutionConfigPort,
        *,
        instance_id: str | None,
        instance_pool: _InstancePoolPort | None,
        artifact_resolver: ArtifactResolutionPort | None = None,
    ) -> str:
        artifact = (config.artifact or "").strip()
        if not artifact:
            raise RuntimeError("engine config must specify either engine_path or artifact")

        resolver = _maybe_resolve_resolver(artifact_resolver)
        instance: EngineRuntimeInstancePort | None = None
        if instance_id:
            if instance_pool is None:
                raise ValueError("instance_id requires an instance_pool")
            instance = instance_pool.get_instance(instance_id)
            if instance is None:
                raise ValueError(f"Instance not found: {instance_id}")
        elif instance_pool is not None:
            instance = instance_pool.ensure_local_instance()

        build_options = dict(config.build_options)
        target_cpu_value = str(build_options.get("target_cpu", "")).strip()
        if not target_cpu_value or target_cpu_value.lower() == "auto":
            if instance is not None and instance.is_ssh:
                detected_cpu = self._support.detect_remote_target_cpu(instance)
                if inspect.isawaitable(detected_cpu):
                    detected_cpu = await detected_cpu
                if not isinstance(detected_cpu, str):
                    raise TypeError(
                        f"detect_remote_target_cpu must return a string (got: {type(detected_cpu).__name__})"
                    )
                build_options["target_cpu"] = detected_cpu
            else:
                build_options["target_cpu"] = detect_target_cpu()

        return str(resolver(artifact, build_options))

    async def _compute_exec_paths(
        self,
        instance: EngineRuntimeInstancePort,
        local_resolved: str,
    ) -> tuple[str, str]:
        if instance.is_ssh:
            path = Path(local_resolved)
            instance_engine_dir = PurePosixPath(instance.config.engine_dir)
            engine_path_for_exec = str(instance_engine_dir / path.parent.name / path.name)
            working_dir_for_exec = str(instance_engine_dir / path.parent.name)
            lock = self._ensure_binary_locks.setdefault(engine_path_for_exec, asyncio.Lock())
            try:
                async with lock:
                    await self._support.ensure_remote_binary(instance, path, engine_path_for_exec)
            except (OSError, RuntimeError) as exc:
                raise RuntimeError(
                    f"Failed to provision engine binary {path} on instance {instance.name}: {exc}"
                ) from exc
            return engine_path_for_exec, working_dir_for_exec

        return local_resolved, str(Path(local_resolved).parent)

    @staticmethod
    def _determine_instance(
        instance_id: str | None,
        instance_pool: _InstancePoolPort | None,
    ) -> EngineRuntimeInstancePort:
        if instance_id:
            if instance_pool is None:
                raise ValueError("instance_id requires an instance_pool")
            instance = instance_pool.get_instance(instance_id)
            if instance is None:
                raise ValueError(f"Instance not found: {instance_id}")
            return instance

        if instance_pool is None:
            raise ValueError(
                "instance_pool is required to create an engine; "
                "inject a local pool (InstancePool.ensure_default_local_pool()) at the CLI/composition boundary"
            )
        return instance_pool.ensure_local_instance()

    @staticmethod
    def _apply_overrides(
        config: Any,
        *,
        extra_options: JsonObject | None,
        go_options: JsonObject | None,
        collect_info_strings: bool | None,
        collect_raw_io: bool | None,
        collect_stderr: bool | None,
        collect_outbound: bool | None,
        option_validation: UsiOptionValidationMode | None,
    ) -> Any:
        if extra_options:
            for key in extra_options:
                if not isinstance(key, str):
                    raise TypeError(f"extra_options keys must be str; got {type(key).__name__}")
            config = config.with_overrides(
                options=_cast_json_object_as_str(extra_options),
                output_dir=project_dirs.output_dir,
                engine_dir=project_dirs.engine_dir,
            )
        if go_options:
            config = config.with_overrides(go_options=_cast_json_object_as_str(go_options))
        if (
            collect_info_strings is not None
            or collect_raw_io is not None
            or collect_stderr is not None
            or collect_outbound is not None
            or option_validation is not None
        ):
            config = config.with_overrides(
                should_collect_info_strings=collect_info_strings,
                should_collect_raw_io=collect_raw_io,
                should_collect_stderr=collect_stderr,
                should_collect_outbound=collect_outbound,
                option_validation_default=option_validation,
            )
        return config.resolve_isready_lock_key()


def _maybe_resolve_resolver(
    resolver: ArtifactResolutionPort | None,
) -> ArtifactResolutionPort:
    if resolver is None:
        raise ValueError("artifact_resolver is required; inject via EngineFactoryService")
    return resolver


def _cast_json_object_as_str(values: Mapping[str, object]) -> JsonObject:
    return {str(key): json_serialize(value) for key, value in values.items()}


__all__ = [
    "EngineRuntimeFactory",
]
