"""Implementation of remote execution helper commands for the CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import traceback
from json import JSONDecodeError
from pathlib import Path

from pydantic import ValidationError

from shogiarena import __version__
from shogiarena._core.contexts.game_session.ports.game_execution_spec import (
    GameExecutionSpec,
    validate_minimum_worker_version,
)
from shogiarena._core.contexts.game_session.ports.game_execution_worker import (
    GameExecutionOutcome,
    GameExecutionWorkerPort,
)
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.shared.kernel.async_completion import complete_before_cancelling
from shogiarena._core.shared.kernel.json_types import JsonObject

from .output import write_line

_STREAM_END: tuple[int, int, None] = (-1, -1, None)


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
        await _emit_json_error(f"spec file not found: {spec_path}")
        raise SystemExit(2)
    try:
        raw = spec_path.read_text(encoding="utf-8")
    except OSError as exc:
        await _emit_json_error(f"failed to read spec file {spec_path}: {exc}")
        raise SystemExit(2) from exc
    try:
        spec = json.loads(raw)
    except JSONDecodeError as exc:
        await _emit_json_error(f"invalid spec JSON: {exc}")
        raise SystemExit(2) from exc
    if not isinstance(spec, dict):
        await _emit_json_error("spec root must be a JSON object")
        raise SystemExit(2)
    try:
        sealed_spec = GameExecutionSpec.model_validate(spec)
    except ValidationError as exc:
        await _emit_json_error(f"invalid GameExecutionSpec JSON: {exc}")
        raise SystemExit(2) from exc
    try:
        validate_minimum_worker_version(sealed_spec, worker_version=__version__)
    except ValueError as exc:
        await _emit_json_error(str(exc))
        raise SystemExit(2) from exc
    rc = await run_game_execution_spec(sealed_spec, execution_root=spec_path.parent)
    if rc != 0:
        raise SystemExit(rc)


def _json_error(message: str, *, trace: str | None = None) -> str:
    payload: JsonObject = {"type": "error", "message": message}
    if trace:
        payload["trace"] = trace
    return json.dumps(payload, ensure_ascii=False)


async def _emit_json_error(message: str, *, trace: str | None = None) -> None:
    await write_line(_json_error(message, trace=trace))


async def _stream_progress(queue: asyncio.Queue[tuple[int, int, str | None]]) -> None:
    """One FIFO writer owns progress, trailing cleanup events and the final envelope."""

    while True:
        item = await queue.get()
        if item is _STREAM_END:
            return
        _num_id, _move_count, payload = item
        if isinstance(payload, str) and payload.startswith("{"):
            await write_line(payload)


def _stop_execution(worker: GameExecutionWorkerPort, execution: asyncio.Task[GameExecutionOutcome]) -> None:
    worker.request_shutdown()
    # Output failure and caller cancellation can race during awaited engine
    # cleanup. Deliver cancellation only once rather than interrupting cleanup.
    if not execution.done() and not execution.cancelling():
        execution.cancel()


async def _finish_execution(
    worker: GameExecutionWorkerPort,
    execution: asyncio.Task[GameExecutionOutcome],
    streamer: asyncio.Task[None],
    queue: asyncio.Queue[tuple[int, int, str | None]],
) -> int:
    try:
        done, _ = await asyncio.wait({execution, streamer}, return_when=asyncio.FIRST_COMPLETED)
        if streamer in done:
            # A broken output channel must stop the game, not keep producing
            # progress or attempt an error write through the same failed sink.
            await streamer
            raise RuntimeError("worker output ended before execution completed")
        try:
            outcome = execution.result()
            payload = json.dumps(outcome.result.model_dump(mode="json"), ensure_ascii=False)
            rc = 0
        except asyncio.CancelledError:
            worker.request_shutdown()
            payload = _json_error("cancelled by signal")
            rc = 1
        except (OSError, RuntimeError, TimeoutError, ValueError, TypeError) as exc:
            worker.request_shutdown()
            payload = _json_error(
                f"remote game failed: {type(exc).__name__}: {exc}".rstrip(),
                trace=traceback.format_exc()[-4000:],
            )
            rc = 1
        queue.put_nowait((0, 0, payload))
        queue.put_nowait(_STREAM_END)
        await streamer
        return rc
    finally:
        try:
            if not execution.done():
                _stop_execution(worker, execution)
                try:
                    await execution
                except asyncio.CancelledError:
                    pass
            elif not execution.cancelled():
                execution.exception()
        finally:
            if not streamer.done():
                queue.put_nowait(_STREAM_END)
            await streamer


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
        await _emit_json_error(str(exc))
        return 2
    missing_refs = sorted(secret_ref for secret_ref in secret_refs if secret_ref not in secret_values)
    if missing_refs:
        await _emit_json_error(f"required secret references are unavailable: {', '.join(missing_refs)}")
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
    execution = asyncio.create_task(
        worker.execute(spec, execution_root=execution_root, progress_queue=progress_q, secret_values=secret_values)
    )
    completion = asyncio.create_task(_finish_execution(worker, execution, streamer, progress_q))
    try:
        return await asyncio.shield(completion)
    except asyncio.CancelledError:
        _stop_execution(worker, execution)
        try:
            await complete_before_cancelling(completion)
        except asyncio.CancelledError:
            pass
        return 1
    finally:
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
