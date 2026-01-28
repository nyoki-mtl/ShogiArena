"""Implementation of remote execution helper commands for the CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import tempfile
from json import JSONDecodeError
from pathlib import Path
from typing import Any

import yaml

from shogiarena.arena.engines.engine_factory import EngineFactory
from shogiarena.arena.engines.time_control import TimeControlLimits
from shogiarena.arena.execution.engine_participant import EngineParticipant
from shogiarena.arena.execution.game_runner import GameRunner
from shogiarena.arena.services.game_control.adjudication import AdjudicationConfig


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

    rc = await run_from_spec(spec)
    if rc != 0:
        raise SystemExit(rc)


def _emit_json_error(message: str, *, trace: str | None = None) -> None:
    payload: dict[str, Any] = {"type": "error", "message": message}
    if trace:
        payload["trace"] = trace
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


async def _stream_progress(queue: asyncio.Queue[tuple[int, int, str | None]]) -> None:
    """Stream JSON payloads to stdout and exit once a result_code arrives."""

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
                and parsed.get("result_code") is not None
            ):
                return


def _write_temp_engine_yaml(engine_path: str, options: dict[str, Any] | None, name: str | None) -> Path:
    data: dict[str, Any] = {
        "name": name or Path(engine_path).stem,
        "engine_path": engine_path,
    }
    if options:
        data["options"] = dict(options)
    tmp_dir = Path(tempfile.mkdtemp(prefix="arena_engine_"))
    tmp_file = tmp_dir / "engine.yaml"
    tmp_file.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return tmp_file


def _prepare_engine_spec(raw: dict[str, Any], label: str) -> tuple[Path, dict[str, Any] | None, str | None]:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} spec must be a JSON object")
    engine_path = raw.get("engine_path")
    if not isinstance(engine_path, str) or not engine_path.strip():
        raise ValueError(f"{label} engine_path must be a non-empty string")
    options = raw.get("options")
    if options is not None and not isinstance(options, dict):
        raise ValueError(f"{label} options must be a JSON object when provided")
    name = raw.get("name")
    if name is not None and not isinstance(name, str):
        raise ValueError(f"{label} name must be a string when provided")
    return Path(engine_path), options, name


async def run_from_spec(spec: dict[str, Any]) -> int:
    progress_q: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    runner = GameRunner(progress_queue=progress_q)

    ctx: dict[str, Any] = {
        "runner": runner,
        "streamer": None,
        "black_engine": None,
        "white_engine": None,
        "black_participant": None,
        "white_participant": None,
    }

    try:
        loop = asyncio.get_running_loop()

        def _on_signal(sig: signal.Signals) -> None:
            try:
                r = ctx.get("runner")
                if r is not None:
                    r.request_shutdown()
            except Exception as exc:  # noqa: BLE001 - diagnostic output only
                sys.stderr.write(f"[signal] failed to request shutdown: {exc}\n")
            try:
                streamer_task = ctx.get("streamer")
                if streamer_task is not None:
                    streamer_task.cancel()
            except Exception as exc:  # noqa: BLE001 - diagnostic output only
                sys.stderr.write(f"[signal] failed to cancel streamer: {exc}\n")
            try:
                sys.stdout.write(
                    json.dumps({"type": "error", "message": f"received signal: {sig.name}"}, ensure_ascii=False) + "\n"
                )
                sys.stdout.flush()
            except Exception as exc:  # noqa: BLE001 - diagnostic output only
                sys.stderr.write(f"[signal] failed to emit error event: {exc}\n")

        def _install_handler(sig: signal.Signals) -> None:
            try:
                loop.add_signal_handler(sig, lambda: _on_signal(sig))
            except Exception as exc:  # noqa: BLE001 - environments may not support signal handlers
                sys.stderr.write(f"[signal] add_signal_handler failed for {str(sig)}: {exc}\n")

        for _sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            _install_handler(_sig)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[signal] signal handler setup failed: {exc}\n")

    max_plies = int(spec.get("max_plies") or 0)
    if max_plies > 0:
        runner.adjudication_config = AdjudicationConfig(
            resign_enabled=False,
            max_plies_enabled=True,
            max_plies=max_plies,
        )

    tc_b = spec.get("time_control", {}).get("black", {})
    tc_w = spec.get("time_control", {}).get("white", {})
    try:
        black_limits = TimeControlLimits(**tc_b) if tc_b else None
        white_limits = TimeControlLimits(**tc_w) if tc_w else None
    except (TypeError, ValueError) as exc:
        _emit_json_error(f"invalid time_control: {exc}")
        return 2
    if black_limits is None or white_limits is None:
        _emit_json_error("time_control limits required for both sides")
        return 2

    black_spec = spec.get("black", {})
    white_spec = spec.get("white", {})

    try:
        black_path, black_options, black_name = _prepare_engine_spec(black_spec, "black")
        white_path, white_options, white_name = _prepare_engine_spec(white_spec, "white")
    except ValueError as exc:
        _emit_json_error(str(exc))
        return 2

    b_yaml = _write_temp_engine_yaml(str(black_path), black_options, black_name)
    w_yaml = _write_temp_engine_yaml(str(white_path), white_options, white_name)

    streamer = asyncio.create_task(_stream_progress(progress_q))
    ctx["streamer"] = streamer

    try:
        black_engine = await EngineFactory.create_engine(b_yaml)
        white_engine = await EngineFactory.create_engine(w_yaml)
        ctx["black_engine"] = black_engine
        ctx["white_engine"] = white_engine

        black_participant = EngineParticipant(
            black_engine,
            name_override=black_spec.get("name"),
            role="black",
        )
        white_participant = EngineParticipant(
            white_engine,
            name_override=white_spec.get("name"),
            role="white",
        )
        ctx["black_participant"] = black_participant
        ctx["white_participant"] = white_participant

        game_id = str(spec.get("game_id") or "game_remote")
        sfen = str(spec.get("initial_sfen") or "startpos")

        await runner.run_game(
            black_participant,
            white_participant,
            initial_sfen=sfen,
            game_id=game_id,
            black_time_control_limits=black_limits,
            white_time_control_limits=white_limits,
        )

        try:
            await asyncio.wait_for(streamer, timeout=2.0)
        except asyncio.TimeoutError:
            sys.stderr.write("[runner] streamer drain timed out; continuing\n")

        return 0
    except asyncio.CancelledError:
        _emit_json_error("cancelled by signal")
        return 1
    except Exception as exc:  # noqa: BLE001 - emit JSON error payload and return non-zero
        try:
            import traceback

            tb = traceback.format_exc()
        except Exception:  # noqa: BLE001
            tb = None
        message = f"remote game failed: {type(exc).__name__}: {exc}".rstrip()
        _emit_json_error(message, trace=tb[-4000:] if tb else None)
        return 1
    finally:
        try:
            streamer.cancel()
        except asyncio.CancelledError:  # noqa: PERF203 - expected when cancelling
            sys.stderr.write("[cleanup] streamer task already cancelled\n")
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[cleanup] failed to cancel streamer: {exc}\n")

        shutdown_tasks = []
        participant = ctx.get("black_participant")
        if participant is not None:
            shutdown_tasks.append(participant.shutdown())
        elif ctx.get("black_engine") is not None:
            shutdown_tasks.append(ctx["black_engine"].close())

        participant = ctx.get("white_participant")
        if participant is not None:
            shutdown_tasks.append(participant.shutdown())
        elif ctx.get("white_engine") is not None:
            shutdown_tasks.append(ctx["white_engine"].close())

        if shutdown_tasks:
            try:
                await asyncio.gather(*shutdown_tasks, return_exceptions=True)
            except asyncio.CancelledError:
                sys.stderr.write("[cleanup] shutdown cancelled\n")


__all__ = ["register", "run_from_spec"]
