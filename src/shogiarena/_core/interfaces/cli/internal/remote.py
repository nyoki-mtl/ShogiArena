"""Implementation of remote execution helper commands for the CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import traceback
from json import JSONDecodeError
from pathlib import Path

from packaging.version import InvalidVersion, Version
from pydantic import ValidationError

from shogiarena import __version__
from shogiarena._core.contexts.game_session.ports.game_execution_spec import GameExecutionSpec
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.shared.kernel.json_types import JsonObject


def register_internal(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "_internal",
        help=argparse.SUPPRESS,
    )
    internal_sub = parser.add_subparsers(dest="internal_command")
    internal_sub.required = True
    register(internal_sub)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "remote-run-pair",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--spec-file", required=True, help="Path to JSON spec file")
    parser.set_defaults(async_handler=_remote_run_pair_command)


async def _remote_run_pair_command(args: argparse.Namespace) -> None:
    spec_path = Path(args.spec_file)
    if not spec_path.exists():
        _emit_json_error(f"spec file not found: {spec_path}")
        raise SystemExit(2)
    try:
        raw = spec_path.read_text(encoding="utf-8")
    except OSError as exc:
        _emit_json_error(f"failed to read spec file {spec_path}: {exc}")
        raise SystemExit(2) from exc
    try:
        spec = json.loads(raw)
    except JSONDecodeError as exc:
        _emit_json_error(f"invalid spec JSON: {exc}")
        raise SystemExit(2) from exc
    if not isinstance(spec, dict):
        _emit_json_error("spec root must be a JSON object")
        raise SystemExit(2)
    try:
        sealed_spec = GameExecutionSpec.model_validate(spec)
    except ValidationError as exc:
        _emit_json_error(f"invalid GameExecutionSpec JSON: {exc}")
        raise SystemExit(2) from exc
    try:
        worker_version = Version(__version__)
        minimum_version = Version(str(sealed_spec.minimum_worker_version))
    except InvalidVersion as exc:
        _emit_json_error(f"invalid worker version contract: {exc}")
        raise SystemExit(2) from exc
    if worker_version < minimum_version:
        _emit_json_error(f"worker version {worker_version} does not satisfy minimum {minimum_version}")
        raise SystemExit(2)
    rc = await run_game_execution_spec(sealed_spec, execution_root=spec_path.parent)
    if rc != 0:
        raise SystemExit(rc)


def _emit_json_error(message: str, *, trace: str | None = None) -> None:
    payload: JsonObject = {"type": "error", "message": message}
    if trace:
        payload["trace"] = trace
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


async def _stream_progress(queue: asyncio.Queue[tuple[int, int, str | None]]) -> None:
    """Stream JSON payloads to stdout and exit once a terminal game_result arrives."""

    while True:
        _num_id, _move_count, payload = await queue.get()
        if isinstance(payload, str) and payload.startswith("{"):
            sys.stdout.write(payload + "\n")
            sys.stdout.flush()
            try:
                parsed = json.loads(payload)
            except JSONDecodeError:
                parsed = None
            if (
                isinstance(parsed, dict)
                and parsed.get("type") == "move_progress"
                and parsed.get("game_result") is not None
            ):
                return


async def run_game_execution_spec(spec: GameExecutionSpec, *, execution_root: Path) -> int:
    """Versioned GameExecutionSpecをcomposition rootの共通workerで実行する。"""

    progress_q: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    worker = build_default_root().game_execution_worker
    secret_refs = {
        *spec.black_engine.process.secret_environment_refs.values(),
        *spec.white_engine.process.secret_environment_refs.values(),
    }
    try:
        secret_values = _load_engine_secret_values(secret_refs)
    except ValueError as exc:
        _emit_json_error(str(exc))
        return 2
    missing_refs = sorted(secret_ref for secret_ref in secret_refs if secret_ref not in secret_values)
    if missing_refs:
        _emit_json_error(f"required secret references are unavailable: {', '.join(missing_refs)}")
        return 2
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    for sig in _worker_signals():
        try:
            loop.add_signal_handler(sig, worker.request_shutdown)
            installed_signals.append(sig)
        except (NotImplementedError, RuntimeError, ValueError, OSError):
            continue
    streamer = asyncio.create_task(_stream_progress(progress_q))
    try:
        outcome = await worker.execute(
            spec,
            execution_root=execution_root,
            progress_queue=progress_q,
            secret_values=secret_values,
        )
        try:
            await asyncio.wait_for(streamer, timeout=2.0)
        except TimeoutError:
            sys.stderr.write("[runner] streamer drain timed out; continuing\n")
        sys.stdout.write(json.dumps(outcome.result.model_dump(mode="json"), ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 0
    except asyncio.CancelledError:
        worker.request_shutdown()
        _emit_json_error("cancelled by signal")
        return 1
    except (OSError, RuntimeError, TimeoutError, ValueError, TypeError) as exc:
        worker.request_shutdown()
        tb = traceback.format_exc()
        message = f"remote game failed: {type(exc).__name__}: {exc}".rstrip()
        _emit_json_error(message, trace=tb[-4000:])
        return 1
    finally:
        streamer.cancel()
        await asyncio.gather(streamer, return_exceptions=True)
        for sig in installed_signals:
            loop.remove_signal_handler(sig)


def _load_engine_secret_values(secret_refs: set[str]) -> dict[str, str]:
    values = {secret_ref: os.environ[secret_ref] for secret_ref in secret_refs if secret_ref in os.environ}
    bundle_path = os.environ.get("SHOGIARENA_ENGINE_SECRET_BUNDLE_FILE")
    if not bundle_path:
        return values
    try:
        payload = json.loads(Path(bundle_path).read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError) as exc:
        raise ValueError("failed to load engine secret bundle") from exc
    if not isinstance(payload, dict):
        raise ValueError("engine secret bundle must contain a JSON object")
    for secret_ref in secret_refs:
        value = payload.get(secret_ref)
        if isinstance(value, str):
            values[secret_ref] = value
    return values


def _worker_signals() -> list[signal.Signals]:
    signals = [signal.SIGINT, signal.SIGTERM]
    sighup = getattr(signal, "SIGHUP", None)
    if sighup is not None:
        signals.append(sighup)
    return signals


__all__ = ["register", "register_internal"]
