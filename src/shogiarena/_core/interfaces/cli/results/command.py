"""CLI commands for inspecting persisted results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from shogiarena._core.contexts.game_session.application.session.run_failure_record_service import (
    RunFailureRecordService,
)
from shogiarena._core.contexts.game_session.application.summary.offline_result_summary_service import (
    OfflineResultSummaryRequest,
)
from shogiarena._core.interfaces.cli.main import CliArgumentError
from shogiarena._core.interfaces.composition_root.default_root import build_offline_result_summary
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.run_manifest_reader import is_resumable_manifest
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int, coerce_str
from shogiarena._core.shared.kernel.serialization import json_serialize


class _CsvWriter(Protocol):
    def writerow(self, row: Sequence[object]) -> object: ...


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "results",
        help="Inspect persisted tournament result artifacts",
    )
    results_sub = parser.add_subparsers(dest="results_command")
    results_sub.required = True
    _register_summary(results_sub)
    _register_verify_provenance(results_sub)


def _register_summary(results_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = results_sub.add_parser(
        "summary",
        help="Summarize a run directory or game.db file",
    )
    parser.add_argument("path", type=Path, help="Run directory or path to game.db")
    parser.add_argument(
        "--format",
        choices=["text", "json", "csv"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.95,
        help="Confidence level for score-rate interval (default: 0.95)",
    )
    parser.add_argument(
        "--engine",
        action="append",
        dest="engines",
        help="Restrict displayed engine summaries by exact engine name (repeatable)",
    )
    parser.set_defaults(handler=_summary_command)


def _register_verify_provenance(results_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = results_sub.add_parser(
        "verify-provenance",
        help="Verify manifest provenance hashes against files on disk",
    )
    parser.add_argument("run_dir", type=Path, help="Run directory containing manifest.json")
    parser.set_defaults(handler=_verify_provenance_command)


def _summary_command(args: argparse.Namespace) -> None:
    input_path = Path(args.path).expanduser()
    db_path, run_dir = _resolve_db_path(input_path)
    metadata = _load_summary_metadata(run_dir=run_dir)
    confidence = float(args.confidence)
    if not 0.0 < confidence < 1.0:
        raise CliArgumentError("--confidence must be in the open interval (0, 1)")
    try:
        summary_obj = build_offline_result_summary(
            db_path=db_path,
            request=OfflineResultSummaryRequest(
                source=str(input_path),
                run_dir=str(run_dir) if run_dir is not None else None,
                shogiarena_version=metadata.shogiarena_version,
                total_scheduled_games=metadata.total_scheduled_games,
                failed_games=metadata.failed_games,
                failures=tuple(metadata.failures),
                failures_by_phase=metadata.failures_by_phase,
                manifest_status=metadata.manifest_status,
                is_resumable=metadata.is_resumable,
                confidence=confidence,
            ),
        )
    except ValueError as exc:
        # e.g. a corrupted game_result value in game.db.
        raise CliArgumentError(f"failed to summarize results from {db_path}: {exc}") from exc
    summary = _coerce_summary_payload(summary_obj)
    engines_filter = set(args.engines or [])
    if engines_filter:
        summary = _filter_summary_engines(summary, engines_filter)

    output_format = str(args.format)
    if output_format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    if output_format == "csv":
        _write_csv(summary)
        return
    print(_format_text(summary))


def _verify_provenance_command(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).expanduser()
    manifest = _load_json_object(run_dir / "manifest.json")
    if manifest is None:
        raise CliArgumentError(f"manifest.json not found in run directory: {run_dir}")
    if manifest.get("status") != "provenance_sealed":
        payload: JsonObject = {
            "schema_version": 1,
            "run_dir": str(run_dir),
            "ok": False,
            "failures": [{"path": "manifest.status", "reason": "manifest_not_sealed"}],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise CliArgumentError("provenance verification requires a sealed manifest")
    failures = _verify_manifest_provenance(manifest)
    payload: JsonObject = {
        "schema_version": 1,
        "run_dir": str(run_dir),
        "ok": not failures,
        "failures": failures,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if failures:
        raise CliArgumentError("provenance verification failed")


class _SummaryMetadata:
    def __init__(
        self,
        *,
        shogiarena_version: str | None,
        total_scheduled_games: int | None,
        failed_games: int | None,
        failures: list[JsonObject] | None = None,
        failures_by_phase: JsonObject | None = None,
        manifest_status: str | None = None,
        is_resumable: bool | None = None,
    ) -> None:
        self.shogiarena_version = shogiarena_version
        self.total_scheduled_games = total_scheduled_games
        self.failed_games = failed_games
        self.failures = failures or []
        self.failures_by_phase = failures_by_phase or {}
        self.manifest_status = manifest_status
        self.is_resumable = is_resumable


def _resolve_db_path(path: Path) -> tuple[Path, Path | None]:
    if path.is_dir():
        db_path = path / "game.db"
        if not db_path.exists():
            raise CliArgumentError(f"game.db not found in run directory: {path}")
        return db_path, path
    if not path.exists():
        raise CliArgumentError(f"result path not found: {path}")
    run_dir = path.parent if (path.parent / "manifest.json").exists() else None
    return path, run_dir


def _load_summary_metadata(*, run_dir: Path | None) -> _SummaryMetadata:
    version = None
    total_scheduled = None
    failed_games = None
    manifest_status = None
    is_resumable = None
    if run_dir is None:
        return _SummaryMetadata(
            shogiarena_version=version,
            total_scheduled_games=total_scheduled,
            failed_games=failed_games,
            failures=[],
            failures_by_phase={},
            manifest_status=manifest_status,
            is_resumable=is_resumable,
        )

    manifest = _load_json_object(run_dir / "manifest.json")
    if manifest is not None:
        manifest_status = coerce_str(manifest.get("status"))
        is_resumable = is_resumable_manifest(manifest)
        version = coerce_str(manifest.get("shogiarena_version"))
        tournament = _object_or_none(manifest.get("tournament"))
        if tournament is not None:
            total_scheduled = coerce_int(tournament.get("total_scheduled_games"))
    elif (run_dir / "game.db").exists():
        raise CliArgumentError(f"manifest.json not found in run directory: {run_dir}")

    failures = RunFailureRecordService().load_failures(run_dir)
    failures_by_phase = RunFailureRecordService.failure_counts_by_phase(failures)
    failed_games = failed_games if failed_games is not None else len(failures)

    return _SummaryMetadata(
        shogiarena_version=version,
        total_scheduled_games=total_scheduled,
        failed_games=failed_games,
        failures=failures,
        failures_by_phase=failures_by_phase,
        manifest_status=manifest_status,
        is_resumable=is_resumable,
    )


def _load_json_object(path: Path) -> JsonObject | None:
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliArgumentError(f"failed to read JSON artifact {path}: {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise CliArgumentError(f"JSON artifact must contain an object: {path}")
    return {str(key): json_serialize(value) for key, value in loaded.items()}


def _verify_manifest_provenance(manifest: JsonObject) -> list[JsonObject]:
    failures: list[JsonObject] = []
    engines_raw = manifest.get("engines")
    if not isinstance(engines_raw, list):
        return [{"path": "manifest.engines", "reason": "missing"}]
    for index, raw_engine in enumerate(engines_raw):
        if not isinstance(raw_engine, Mapping):
            failures.append({"path": f"manifest.engines[{index}]", "reason": "not_object"})
            continue
        engine = {str(key): json_serialize(value) for key, value in raw_engine.items()}
        engine_name = coerce_str(engine.get("name")) or f"engine[{index}]"
        resolved_paths = _object_or_none(engine.get("resolved_paths")) or {}
        bytes_hash = _object_or_none(engine.get("bytes_hash")) or {}
        binary_path = coerce_str(resolved_paths.get("engine_path"))
        expected_binary = coerce_str(bytes_hash.get("engine_binary_sha256"))
        actual_binary = _sha256_path(Path(binary_path)) if binary_path else None
        if expected_binary and actual_binary != expected_binary:
            failures.append(
                {
                    "engine": engine_name,
                    "path": binary_path,
                    "reason": "engine_binary_sha256_mismatch",
                    "expected": expected_binary,
                    "actual": actual_binary,
                }
            )
        path_hashes = _object_or_none(bytes_hash.get("path_options")) or {}
        option_paths = _object_or_none(resolved_paths.get("path_options")) or {}
        for option_name, raw_expected in path_hashes.items():
            expected = coerce_str(raw_expected)
            option_path = coerce_str(option_paths.get(option_name))
            actual = _sha256_path(Path(option_path)) if option_path else None
            if expected and actual != expected:
                failures.append(
                    {
                        "engine": engine_name,
                        "option": option_name,
                        "path": option_path,
                        "reason": "path_option_sha256_mismatch",
                        "expected": expected,
                        "actual": actual,
                    }
                )
    return failures


def _sha256_path(path: Path) -> str | None:
    if path.is_file():
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return None
    if not path.is_dir():
        return None
    try:
        digest = hashlib.sha256()
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            rel = child.relative_to(path).as_posix()
            child_hash = _sha256_path(child) or ""
            stat = child.stat()
            digest.update(rel.encode("utf-8"))
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(child_hash.encode("ascii"))
        return digest.hexdigest()
    except OSError:
        return None


def _coerce_summary_payload(summary: object) -> JsonObject:
    to_payload = getattr(summary, "to_payload", None)
    if not callable(to_payload):
        raise CliArgumentError("summary service returned an unsupported object")
    payload = to_payload()
    if not isinstance(payload, Mapping):
        raise CliArgumentError("summary service returned a non-object payload")
    return {str(key): json_serialize(value) for key, value in payload.items()}


def _object_or_none(value: object) -> JsonObject | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): json_serialize(item) for key, item in value.items()}


def _filter_summary_engines(summary: JsonObject, engines: set[str]) -> JsonObject:
    raw_engines = summary.get("engines")
    if not isinstance(raw_engines, list):
        return summary
    filtered: list[JsonObject] = []
    for engine in raw_engines:
        if not isinstance(engine, Mapping):
            continue
        engine_name = coerce_str(engine.get("engine"))
        if engine_name in engines:
            filtered.append({str(key): json_serialize(value) for key, value in engine.items()})
    next_summary = dict(summary)
    next_summary["engines"] = filtered
    return next_summary


def _format_text(summary: JsonObject) -> str:
    lines: list[str] = []
    lines.append("Result Summary")
    run_dir = coerce_str(summary.get("run_dir"))
    version = coerce_str(summary.get("shogiarena_version"))
    total_scheduled = coerce_int(summary.get("total_scheduled_games"))
    completed = coerce_int(summary.get("completed_games")) or 0
    incomplete = coerce_int(summary.get("incomplete_games"))
    failed = coerce_int(summary.get("failed_games"))
    draw_rate = _coerce_float_or_none(summary.get("draw_rate"))
    if run_dir is not None:
        lines.append(f"Run directory: {run_dir}")
    if version is not None:
        lines.append(f"ShogiArena version: {version}")
    if total_scheduled is not None:
        lines.append(f"Total scheduled games: {total_scheduled}")
    lines.append(f"Completed games: {completed}")
    if incomplete is not None:
        lines.append(f"Incomplete games: {incomplete}")
    if failed is not None:
        lines.append(f"Failed/cancelled games: {failed}")
    if draw_rate is not None:
        lines.append(f"Draw rate: {_format_rate(draw_rate)}")
    timing_metrics = _object_or_none(summary.get("timing_metrics")) or {}
    engine_wall_field = coerce_str(timing_metrics.get("engine_throughput_wall_time_field"))
    clock_wall_field = coerce_str(timing_metrics.get("clock_charged_wall_time_field"))
    wall_nps_field = coerce_str(timing_metrics.get("wall_nps_default_time_field"))
    if engine_wall_field is not None and clock_wall_field is not None:
        lines.append(
            "Timing metrics: "
            f"engine throughput wall={engine_wall_field}, "
            f"clock-charged wall={clock_wall_field}, "
            f"wall NPS default={wall_nps_field or engine_wall_field}"
        )
    failures_by_phase = _object_or_none(summary.get("failures_by_phase")) or {}
    if failures_by_phase:
        lines.append("Failures by phase:")
        for phase, count in sorted(failures_by_phase.items()):
            lines.append(f"  {phase}: {count}")
    lines.append("")
    lines.append("Engines:")
    confidence = _coerce_float_or_none(summary.get("confidence")) or 0.95
    raw_engines = summary.get("engines")
    if isinstance(raw_engines, list):
        for engine in raw_engines:
            if isinstance(engine, Mapping):
                engine_payload = {str(key): json_serialize(value) for key, value in engine.items()}
                lines.extend(_format_engine_text(engine_payload, confidence=confidence))
    lines.append("Raw game_result counts:")
    raw_counts = _object_or_none(summary.get("raw_result_counts")) or {}
    for result, count in sorted(raw_counts.items()):
        lines.append(f"  {result}: {count}")
    return "\n".join(lines)


def _format_engine_text(engine: JsonObject, *, confidence: float) -> list[str]:
    engine_name = coerce_str(engine.get("engine")) or "<unknown>"
    wins = coerce_int(engine.get("wins")) or 0
    draws = coerce_int(engine.get("draws")) or 0
    losses = coerce_int(engine.get("losses")) or 0
    score_rate = _coerce_float_or_none(engine.get("score_rate"))
    non_draw_win = _coerce_float_or_none(engine.get("win_rate_excluding_draws"))
    interval = _object_or_none(engine.get("score_confidence_interval"))
    interval_text = "n/a"
    if interval is not None:
        interval_text = (
            f"{_format_rate(_coerce_float_or_none(interval.get('low')))}"
            f"..{_format_rate(_coerce_float_or_none(interval.get('high')))}"
        )
    side_split = _object_or_none(engine.get("side_split")) or {}
    black = _object_or_none(side_split.get("black")) or {}
    white = _object_or_none(side_split.get("white")) or {}
    return [
        (
            f"  {engine_name}: {wins}/{draws}/{losses} "
            f"score={_format_rate(score_rate)} "
            f"non_draw_win={_format_rate(non_draw_win)} "
            f"CI{confidence:.2f}={interval_text}"
        ),
        (
            f"    black: {coerce_int(black.get('wins')) or 0}/{coerce_int(black.get('draws')) or 0}/"
            f"{coerce_int(black.get('losses')) or 0} "
            f"score={_format_rate(_coerce_float_or_none(black.get('score_rate')))}"
        ),
        (
            f"    white: {coerce_int(white.get('wins')) or 0}/{coerce_int(white.get('draws')) or 0}/"
            f"{coerce_int(white.get('losses')) or 0} "
            f"score={_format_rate(_coerce_float_or_none(white.get('score_rate')))}"
        ),
    ]


def _format_rate(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.2f}%"


def _coerce_float_or_none(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _csv_float(value: object) -> float | str:
    parsed = _coerce_float_or_none(value)
    return parsed if parsed is not None else ""


def _write_csv(summary: JsonObject) -> None:
    writer = csv.writer(sys.stdout)
    writer.writerow(
        [
            "record_type",
            "engine",
            "side",
            "name",
            "games",
            "wins",
            "draws",
            "losses",
            "score_rate",
            "win_rate_excluding_draws",
            "draw_rate",
            "ci_low",
            "ci_high",
            "count",
        ]
    )
    raw_engines = summary.get("engines")
    if isinstance(raw_engines, list):
        for engine in raw_engines:
            if not isinstance(engine, Mapping):
                continue
            engine_payload = {str(key): json_serialize(value) for key, value in engine.items()}
            _write_engine_csv_row(writer, engine_payload, side="all")
            _write_engine_csv_row(writer, engine_payload, side="black")
            _write_engine_csv_row(writer, engine_payload, side="white")
    raw_result_counts = _object_or_none(summary.get("raw_result_counts")) or {}
    for raw_result, count in sorted(raw_result_counts.items()):
        writer.writerow(["raw_result", "", "", raw_result, "", "", "", "", "", "", "", "", "", count])
    failures_by_phase = _object_or_none(summary.get("failures_by_phase")) or {}
    for phase, count in sorted(failures_by_phase.items()):
        writer.writerow(["failure_phase", "", "", phase, "", "", "", "", "", "", "", "", "", count])


def _write_engine_csv_row(writer: _CsvWriter, engine: JsonObject, *, side: str) -> None:
    wdl = engine
    ci_low: object = ""
    ci_high: object = ""
    side_split = _object_or_none(engine.get("side_split")) or {}
    if side == "black":
        wdl = _object_or_none(side_split.get("black")) or {}
    elif side == "white":
        wdl = _object_or_none(side_split.get("white")) or {}
    else:
        interval = _object_or_none(engine.get("score_confidence_interval"))
        if interval is not None:
            ci_low = _csv_float(interval.get("low"))
            ci_high = _csv_float(interval.get("high"))
    writer.writerow(
        [
            "engine" if side == "all" else "side",
            coerce_str(engine.get("engine")) or "",
            side,
            "",
            coerce_int(wdl.get("games")) or 0,
            coerce_int(wdl.get("wins")) or 0,
            coerce_int(wdl.get("draws")) or 0,
            coerce_int(wdl.get("losses")) or 0,
            _csv_float(wdl.get("score_rate")),
            _csv_float(wdl.get("win_rate_excluding_draws")),
            _csv_float(wdl.get("draw_rate")),
            ci_low,
            ci_high,
            "",
        ]
    )


__all__ = ["register"]
