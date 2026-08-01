"""SPSA 実行前の YaneuraOu fixed option 検査。"""

from __future__ import annotations

import json
import shlex
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol, TypeAlias

from shogiarena._core.contexts.instances.application.instance_models import Instance
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.contexts.instances.application.ssh_transport import SshTransport, create_transport
from shogiarena._core.platform.engine_runtime.usi_config import UsiEngineConfig
from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.yaneuraou_option_files import (
    YaneuraOuOptionEntry,
    parse_yaneuraou_option_lines,
)

FIXED_OPTION_PREFLIGHT_SCHEMA_VERSION = "shogiarena.spsa.fixed_option_preflight.v2"
FIXED_OPTION_PREFLIGHT_FILENAME = "fixed_option_preflight.json"

FixedOptionFileStatus: TypeAlias = Literal["missing", "checked", "conflict"]
FixedOptionEngineStatus: TypeAlias = Literal["checked", "skipped", "conflict"]


class _EngineConfigPort(Protocol):
    @property
    def name(self) -> str | None: ...

    @property
    def engine_path(self) -> Path | None: ...

    @property
    def artifact(self) -> str | None: ...

    @property
    def options(self) -> Mapping[str, object]: ...

    @property
    def instance_id(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class FixedOptionFileScan:
    """YaneuraOu option file の検査結果。"""

    path: Path | PurePosixPath
    status: FixedOptionFileStatus
    option_names: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

    def to_json(self) -> JsonObject:
        """JSON artifact 用の payload に変換する。"""

        return {
            "path": str(self.path),
            "status": self.status,
            "option_names": list(self.option_names),
            "conflicts": list(self.conflicts),
        }


@dataclass(frozen=True, slots=True)
class FixedOptionEngineScan:
    """SPSA 対象エンジン単位の fixed option 検査結果。"""

    engine_name: str
    status: FixedOptionEngineStatus
    reason: str | None
    engine_config_path: Path | None
    engine_path: str | None
    engine_directory: Path | None
    static_option_names: tuple[str, ...] = ()
    static_option_conflicts: tuple[str, ...] = ()
    local_source_status: Literal["checked", "unavailable"] = "unavailable"
    runtime_evidence_scope: Literal["local_runtime", "remote_runtime"] = "local_runtime"
    runtime_evidence_status: Literal["covered_by_local_source", "verified", "unverified", "unavailable"] = (
        "covered_by_local_source"
    )
    option_files: tuple[FixedOptionFileScan, ...] = ()
    runtime_option_files: tuple[FixedOptionFileScan, ...] = ()

    @property
    def conflicts(self) -> tuple[FixedOptionFileScan, ...]:
        """競合が見つかった option file の検査結果を返す。"""

        return tuple(scan for scan in (*self.option_files, *self.runtime_option_files) if scan.conflicts)

    def to_json(self) -> JsonObject:
        """JSON artifact 用の payload に変換する。"""

        payload: JsonObject = {
            "engine_name": self.engine_name,
            "status": self.status,
            "reason": self.reason,
            "engine_config_path": str(self.engine_config_path) if self.engine_config_path is not None else None,
            "engine_path": self.engine_path,
            "engine_directory": str(self.engine_directory) if self.engine_directory is not None else None,
            "static_options": {
                "scope": "config_static",
                "option_names": list(self.static_option_names),
                "conflicts": list(self.static_option_conflicts),
            },
            "local_source_evidence": {
                "scope": "local_source",
                "status": self.local_source_status,
            },
            "runtime_evidence": {
                "scope": self.runtime_evidence_scope,
                "status": self.runtime_evidence_status,
                "option_files": [scan.to_json() for scan in self.runtime_option_files],
            },
            "option_files": [scan.to_json() for scan in self.option_files],
        }
        return payload


@dataclass(frozen=True, slots=True)
class FixedOptionPreflightReport:
    """SPSA fixed option preflight 全体の検査結果。"""

    target_option_names: tuple[str, ...]
    engines: tuple[FixedOptionEngineScan, ...]

    @property
    def has_conflicts(self) -> bool:
        """いずれかの対象エンジンで fixed option 競合があれば ``True`` を返す。"""

        return any(engine.static_option_conflicts or engine.conflicts for engine in self.engines)

    @property
    def has_unverified_remote(self) -> bool:
        """Remote option files that could not be inspected must block execution."""

        return any(engine.runtime_evidence_status == "unverified" for engine in self.engines)

    @property
    def status(self) -> Literal["passed", "unverified_remote", "conflict"]:
        """Dashboardへ投影するpreflight statusを返す。"""

        if self.has_conflicts:
            return "conflict"
        if self.has_unverified_remote:
            return "unverified_remote"
        return "passed"

    def to_json(self) -> JsonObject:
        """JSON artifact 用の payload に変換する。"""

        return {
            "schema_version": FIXED_OPTION_PREFLIGHT_SCHEMA_VERSION,
            "status": self.status,
            "target_option_names": list(self.target_option_names),
            "engines": [engine.to_json() for engine in self.engines],
        }


def run_yaneuraou_fixed_option_preflight(
    *,
    engines: Sequence[_EngineConfigPort],
    target_option_names: Iterable[str],
    run_dir: Path,
    output_dir: Path,
    engine_dir: Path,
    instance_pool: InstancePool | None = None,
) -> FixedOptionPreflightReport:
    """SPSA 対象 option が YaneuraOu fixed option に含まれないことを検査する。"""

    normalized_targets = _normalize_target_option_names(target_option_names)
    artifact_path = run_dir / "spsa" / FIXED_OPTION_PREFLIGHT_FILENAME
    try:
        engine_scans = tuple(
            _scan_engine(
                engine,
                target_option_names=normalized_targets,
                output_dir=output_dir,
                engine_dir=engine_dir,
                runtime_scope=_resolve_runtime_scope(engine, instance_pool),
            )
            for engine in engines
        )
    except Exception as exc:
        write_json_atomic(
            artifact_path,
            {
                "schema_version": FIXED_OPTION_PREFLIGHT_SCHEMA_VERSION,
                "status": "failed",
                "target_option_names": list(normalized_targets),
                "failure": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
                "engines": [],
            },
        )
        raise
    report = FixedOptionPreflightReport(target_option_names=normalized_targets, engines=engine_scans)
    write_json_atomic(artifact_path, report.to_json())
    if report.has_conflicts:
        raise ValueError(_format_conflict_message(report))
    if report.has_unverified_remote:
        raise ValueError(
            "SPSA remote execution requires verified runtime fixed-option files; "
            "remote engine_options.txt/eval_options.txt evidence is unavailable"
        )
    return report


async def run_verified_spsa_fixed_option_preflight(
    *,
    engines: Sequence[_EngineConfigPort],
    target_option_names: Iterable[str],
    run_dir: Path,
    output_dir: Path,
    engine_dir: Path,
    instance_pool: InstancePool,
) -> FixedOptionPreflightReport:
    """Scan local sources and the actual SSH runtime file set."""

    normalized_targets = _normalize_target_option_names(target_option_names)
    artifact_path = run_dir / "spsa" / FIXED_OPTION_PREFLIGHT_FILENAME
    try:
        engine_scans: list[FixedOptionEngineScan] = []
        for engine in engines:
            runtime_scope = _resolve_runtime_scope(engine, instance_pool)
            local_scan = _scan_engine(
                engine,
                target_option_names=normalized_targets,
                output_dir=output_dir,
                engine_dir=engine_dir,
                runtime_scope=runtime_scope,
            )
            if runtime_scope == "local_runtime":
                engine_scans.append(local_scan)
                continue
            instance_id = engine.instance_id
            assert instance_id is not None
            instance = instance_pool.get_instance(instance_id)
            if instance is None:
                raise ValueError(f"SPSA remote fixed-option preflight instance is unavailable: {instance_id}")
            runtime_files = await _scan_remote_engine_directory(
                engine,
                instance=instance,
                target_option_names=normalized_targets,
                output_dir=output_dir,
                engine_dir=engine_dir,
            )
            status: FixedOptionEngineStatus = (
                "conflict"
                if local_scan.static_option_conflicts
                or local_scan.conflicts
                or any(scan.conflicts for scan in runtime_files)
                else "checked"
            )
            engine_scans.append(
                replace(
                    local_scan,
                    status=status,
                    runtime_evidence_status="verified",
                    runtime_option_files=runtime_files,
                )
            )
    except Exception as exc:
        write_json_atomic(
            artifact_path,
            {
                "schema_version": FIXED_OPTION_PREFLIGHT_SCHEMA_VERSION,
                "status": "failed",
                "target_option_names": list(normalized_targets),
                "failure": {"type": type(exc).__name__, "message": str(exc)},
                "engines": [],
            },
        )
        raise
    report = FixedOptionPreflightReport(
        target_option_names=normalized_targets,
        engines=tuple(engine_scans),
    )
    write_json_atomic(artifact_path, report.to_json())
    if report.has_conflicts:
        raise ValueError(_format_conflict_message(report))
    if report.has_unverified_remote:
        raise ValueError(
            "SPSA remote execution requires verified runtime fixed-option files; "
            "remote engine_options.txt/eval_options.txt evidence is unavailable"
        )
    return report


def validate_sealed_fixed_option_evidence(
    *,
    evidence: Mapping[str, object],
    target_option_names: Iterable[str],
) -> JsonObject:
    """Resume用のsealed fixed-option evidenceをread-onlyで検証する。"""

    payload = coerce_json_object_serialized(evidence, field_name="fixed_option_preflight")
    if payload.get("schema_version") != FIXED_OPTION_PREFLIGHT_SCHEMA_VERSION:
        raise ValueError("SPSA sealed fixed-option preflight schema is unsupported")
    if payload.get("status") != "passed":
        raise ValueError("SPSA sealed fixed-option preflight did not pass")
    expected_targets = list(_normalize_target_option_names(target_option_names))
    if payload.get("target_option_names") != expected_targets:
        raise ValueError("SPSA sealed fixed-option preflight target options mismatch")
    return payload


def load_fixed_option_preflight_status(run_dir: Path) -> JsonObject:
    """Dashboard用にfixed-option preflightのstatusだけを安全に読む。"""

    artifact_path = run_dir / "spsa" / FIXED_OPTION_PREFLIGHT_FILENAME
    try:
        raw = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"fixed_options": "unknown"}
    if not isinstance(raw, dict):
        return {"fixed_options": "unknown"}
    status = raw.get("status")
    if status not in {"passed", "unverified_remote", "conflict", "failed"}:
        status = "unknown"
    return {"fixed_options": status}


def _normalize_target_option_names(target_option_names: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    normalized: list[str] = []
    for option_name in target_option_names:
        stripped = str(option_name).strip()
        if not stripped:
            continue
        folded = stripped.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        normalized.append(stripped)
    return tuple(normalized)


def _resolve_runtime_scope(
    engine: _EngineConfigPort,
    instance_pool: InstancePool | None,
) -> Literal["local_runtime", "remote_runtime"]:
    instance_id = engine.instance_id
    if not instance_id:
        return "local_runtime"
    if instance_pool is None:
        return "remote_runtime"
    instance = instance_pool.get_instance(instance_id)
    if instance is None:
        raise ValueError(f"SPSA fixed-option preflight instance is unavailable: {instance_id}")
    return "remote_runtime" if instance.is_ssh else "local_runtime"


def _scan_engine(
    engine: _EngineConfigPort,
    *,
    target_option_names: tuple[str, ...],
    output_dir: Path,
    engine_dir: Path,
    runtime_scope: Literal["local_runtime", "remote_runtime"],
) -> FixedOptionEngineScan:
    engine_name = str(engine.name or engine.artifact or "engine")
    config_static_options = tuple(str(name) for name in getattr(engine, "options", {}))
    engine_config_path = engine.engine_path
    if engine_config_path is None:
        return _skipped_engine_scan(
            engine_name,
            "engine_config_path_unavailable",
            None,
            None,
            target_option_names=target_option_names,
            static_option_names=config_static_options,
            runtime_scope=runtime_scope,
        )
    if _looks_remote_path(str(engine_config_path)):
        return _skipped_engine_scan(
            engine_name,
            "remote_engine_config_path",
            engine_config_path,
            None,
            target_option_names=target_option_names,
            static_option_names=config_static_options,
            runtime_scope=runtime_scope,
        )
    if not engine_config_path.exists():
        return _skipped_engine_scan(
            engine_name,
            "engine_config_path_missing",
            engine_config_path,
            None,
            target_option_names=target_option_names,
            static_option_names=config_static_options,
            runtime_scope=runtime_scope,
        )

    usi_config = UsiEngineConfig.from_file(
        engine_config_path,
        output_dir=output_dir,
        engine_dir=engine_dir,
    )
    static_option_names = tuple(dict.fromkeys((*usi_config.options, *config_static_options)))
    static_option_conflicts = _find_conflicting_option_names(target_option_names, static_option_names)
    resolved_engine_path = usi_config.engine_path
    if resolved_engine_path is None or not resolved_engine_path.strip():
        return _skipped_engine_scan(
            engine_name,
            "engine_path_unavailable",
            engine_config_path,
            None,
            target_option_names=target_option_names,
            static_option_names=static_option_names,
            runtime_scope=runtime_scope,
        )
    if _looks_remote_path(resolved_engine_path):
        return _skipped_engine_scan(
            engine_name,
            "remote_engine_path",
            engine_config_path,
            resolved_engine_path,
            target_option_names=target_option_names,
            static_option_names=static_option_names,
            runtime_scope="remote_runtime",
        )

    engine_directory = Path(resolved_engine_path).expanduser().resolve().parent
    option_files = _scan_engine_directory(engine_directory, target_option_names=target_option_names)
    status: FixedOptionEngineStatus = (
        "conflict" if static_option_conflicts or any(scan.conflicts for scan in option_files) else "checked"
    )
    return FixedOptionEngineScan(
        engine_name=engine_name,
        status=status,
        reason=None,
        engine_config_path=engine_config_path,
        engine_path=resolved_engine_path,
        engine_directory=engine_directory,
        static_option_names=static_option_names,
        static_option_conflicts=static_option_conflicts,
        local_source_status="checked",
        runtime_evidence_scope=runtime_scope,
        runtime_evidence_status=("unverified" if runtime_scope == "remote_runtime" else "covered_by_local_source"),
        option_files=option_files,
    )


def _skipped_engine_scan(
    engine_name: str,
    reason: str,
    engine_config_path: Path | None,
    engine_path: str | None,
    *,
    target_option_names: tuple[str, ...],
    static_option_names: tuple[str, ...],
    runtime_scope: Literal["local_runtime", "remote_runtime"],
) -> FixedOptionEngineScan:
    static_option_conflicts = _find_conflicting_option_names(target_option_names, static_option_names)
    return FixedOptionEngineScan(
        engine_name=engine_name,
        status="conflict" if static_option_conflicts else "skipped",
        reason=reason,
        engine_config_path=engine_config_path,
        engine_path=engine_path,
        engine_directory=None,
        static_option_names=static_option_names,
        static_option_conflicts=static_option_conflicts,
        local_source_status="unavailable",
        runtime_evidence_scope=runtime_scope,
        runtime_evidence_status=("unverified" if runtime_scope == "remote_runtime" else "unavailable"),
        option_files=(),
    )


def _scan_engine_directory(
    engine_directory: Path,
    *,
    target_option_names: tuple[str, ...],
) -> tuple[FixedOptionFileScan, ...]:
    engine_options_path = engine_directory / "engine_options.txt"
    engine_scan, engine_entries = _scan_option_file(engine_options_path, target_option_names=target_option_names)
    eval_dir = _find_option_value(engine_entries, "EvalDir")
    if eval_dir is not None and eval_dir.strip():
        eval_path = Path(eval_dir).expanduser()
        if not eval_path.is_absolute():
            eval_path = engine_directory / eval_path
        eval_options_path = eval_path / "eval_options.txt"
    else:
        eval_options_path = engine_directory / "eval" / "eval_options.txt"
    eval_scan, _eval_entries = _scan_option_file(eval_options_path, target_option_names=target_option_names)
    return (engine_scan, eval_scan)


def _scan_option_file(
    path: Path,
    *,
    target_option_names: tuple[str, ...],
) -> tuple[FixedOptionFileScan, tuple[YaneuraOuOptionEntry, ...]]:
    if not path.exists():
        return FixedOptionFileScan(path=path, status="missing"), ()
    entries = parse_yaneuraou_option_lines(path.read_text(encoding="utf-8").splitlines())
    option_names = tuple(entry.name for entry in entries)
    conflicts = _find_conflicting_option_names(target_option_names, option_names)
    status: FixedOptionFileStatus = "conflict" if conflicts else "checked"
    return (
        FixedOptionFileScan(
            path=path,
            status=status,
            option_names=option_names,
            conflicts=conflicts,
        ),
        entries,
    )


async def _scan_remote_engine_directory(
    engine: _EngineConfigPort,
    *,
    instance: Instance,
    target_option_names: tuple[str, ...],
    output_dir: Path,
    engine_dir: Path,
) -> tuple[FixedOptionFileScan, ...]:
    engine_config_path = engine.engine_path
    if engine_config_path is None:
        raise ValueError("SPSA remote fixed-option preflight requires an engine config path")
    runtime_config = UsiEngineConfig.from_file(
        engine_config_path,
        output_dir=output_dir,
        engine_dir=engine_dir,
    )
    if runtime_config.engine_path is None:
        raise ValueError("SPSA remote fixed-option preflight requires a resolved engine binary")
    remote_engine_dir = PurePosixPath(instance.config.engine_dir) / Path(runtime_config.engine_path).parent.name
    transport = create_transport(instance)
    await transport.connect()
    try:
        engine_scan, engine_entries = await _scan_remote_option_file(
            transport,
            remote_engine_dir / "engine_options.txt",
            target_option_names=target_option_names,
        )
        eval_dir = _find_option_value(engine_entries, "EvalDir")
        if eval_dir is not None and eval_dir.strip():
            relative_eval_path = PurePosixPath(eval_dir)
            if relative_eval_path.is_absolute() or ".." in relative_eval_path.parts:
                raise ValueError("Remote EvalDir must be relative to the provisioned engine directory")
            eval_path = remote_engine_dir / relative_eval_path
        else:
            eval_path = remote_engine_dir / "eval"
        eval_scan, _eval_entries = await _scan_remote_option_file(
            transport,
            eval_path / "eval_options.txt",
            target_option_names=target_option_names,
        )
        return (engine_scan, eval_scan)
    finally:
        await transport.close()


async def _scan_remote_option_file(
    transport: SshTransport,
    path: PurePosixPath,
    *,
    target_option_names: tuple[str, ...],
) -> tuple[FixedOptionFileScan, tuple[YaneuraOuOptionEntry, ...]]:
    quoted = shlex.quote(str(path))
    rc, stdout, stderr = await transport.run(f"if [ -f {quoted} ]; then cat -- {quoted}; else exit 44; fi")
    if rc == 44:
        return FixedOptionFileScan(path=path, status="missing"), ()
    if rc != 0:
        raise RuntimeError(f"failed to inspect remote fixed-option file {path}: {stderr.strip() or rc}")
    entries = parse_yaneuraou_option_lines(stdout.splitlines())
    option_names = tuple(entry.name for entry in entries)
    conflicts = _find_conflicting_option_names(target_option_names, option_names)
    return (
        FixedOptionFileScan(
            path=path,
            status="conflict" if conflicts else "checked",
            option_names=option_names,
            conflicts=conflicts,
        ),
        entries,
    )


def _find_option_value(entries: Sequence[YaneuraOuOptionEntry], option_name: str) -> str | None:
    folded = option_name.casefold()
    for entry in entries:
        if entry.name.casefold() == folded:
            return entry.value
    return None


def _find_conflicting_option_names(
    target_option_names: tuple[str, ...],
    fixed_option_names: tuple[str, ...],
) -> tuple[str, ...]:
    targets_by_folded = {name.casefold(): name for name in target_option_names}
    fixed_folded = {name.casefold() for name in fixed_option_names}
    return tuple(targets_by_folded[key] for key in sorted(targets_by_folded.keys() & fixed_folded))


def _looks_remote_path(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized.startswith(("ssh://", "ssh:"))


def _format_conflict_message(report: FixedOptionPreflightReport) -> str:
    lines = [
        "SPSA target option(s) conflict with base static options or YaneuraOu fixed option files.",
        "Remove the static/fixed entries or exclude them from the SPSA space:",
    ]
    for engine in report.engines:
        if engine.static_option_conflicts:
            lines.append(f"  {engine.engine_name}: config static options: {', '.join(engine.static_option_conflicts)}")
        for scan in engine.conflicts:
            lines.append(f"  {engine.engine_name}: {scan.path}: {', '.join(scan.conflicts)}")
    return "\n".join(lines)


__all__ = [
    "FIXED_OPTION_PREFLIGHT_FILENAME",
    "FIXED_OPTION_PREFLIGHT_SCHEMA_VERSION",
    "FixedOptionEngineScan",
    "FixedOptionFileScan",
    "FixedOptionPreflightReport",
    "load_fixed_option_preflight_status",
    "run_yaneuraou_fixed_option_preflight",
    "run_verified_spsa_fixed_option_preflight",
    "validate_sealed_fixed_option_evidence",
]
