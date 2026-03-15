"""Implementation of remote execution helper commands for the CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import tempfile
import traceback
from collections.abc import Awaitable, Mapping
from json import JSONDecodeError
from pathlib import Path
from typing import Protocol, TypedDict

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from rshogi.types import Color

from shogiarena._core.contexts.match.application.adjudication_builders import max_plies_only_adjudication
from shogiarena._core.contexts.match.application.engine_participant import EngineParticipant
from shogiarena._core.contexts.match.application.runner import GameRunner
from shogiarena._core.interfaces.cli.run.engine_loader import load_engine
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonScalar, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import OptionalText, coerce_int
from shogiarena._core.shared.kernel.serialization import json_serialize
from shogiarena._core.shared.kernel.time_control import TimeControlLimits


class _ClosableEnginePort(Protocol):
    async def close(self) -> None: ...


class _RemoteEngineSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    engine_path: str
    options: dict[str, JsonScalar] | None = None
    name: str | None = None

    @field_validator("engine_path", mode="before")
    @classmethod
    def _coerce_engine_path(cls, value: JsonValue | None) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("engine_path must be a non-empty string")
        return normalized

    @field_validator("options", mode="before")
    @classmethod
    def _coerce_options(cls, value: JsonValue | Mapping[str, JsonValue] | None) -> dict[str, JsonScalar] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise TypeError("options must be a JSON object when provided")
        normalized: dict[str, JsonScalar] = {}
        for key, item in value.items():
            serialized = json_serialize(item)
            if isinstance(serialized, dict | list):
                raise TypeError("options values must be JSON scalar values")
            normalized[str(key)] = serialized
        return normalized

    @field_validator("name", mode="before")
    @classmethod
    def _coerce_name(cls, value: JsonValue | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class _RemoteTimeControlSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    black: dict[str, JsonScalar]
    white: dict[str, JsonScalar]

    @field_validator("black", "white", mode="before")
    @classmethod
    def _coerce_side_tc(cls, value: JsonValue | Mapping[str, JsonValue] | None) -> dict[str, JsonScalar]:
        if not isinstance(value, dict):
            raise TypeError("time_control.black/white must be JSON objects")
        normalized: dict[str, JsonScalar] = {}
        for key, item in value.items():
            serialized = json_serialize(item)
            if isinstance(serialized, dict | list):
                raise TypeError("time_control values must be JSON scalar values")
            normalized[str(key)] = serialized
        return normalized


class _RemotePairSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    game_id: OptionalText = None
    initial_sfen: OptionalText = None
    max_plies: int = 0
    time_control: _RemoteTimeControlSpec
    black: _RemoteEngineSpec
    white: _RemoteEngineSpec

    @field_validator("max_plies", mode="before")
    @classmethod
    def _coerce_max_plies(cls, value: JsonValue | None) -> int:
        if value is None:
            return 0
        parsed = coerce_int(value)
        if parsed is None:
            raise ValueError("max_plies must be an integer")
        return max(0, parsed)


class _RunContext(TypedDict):
    runner: GameRunner
    streamer: asyncio.Task[None] | None
    black_engine: _ClosableEnginePort | None
    white_engine: _ClosableEnginePort | None
    black_participant: EngineParticipant | None
    white_participant: EngineParticipant | None


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
        validated = _RemotePairSpec.model_validate(spec)
    except ValidationError as exc:
        _emit_json_error(f"invalid spec JSON: {exc}")
        raise SystemExit(2) from exc

    rc = await run_from_spec(validated)
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


def _write_temp_engine_yaml(engine_path: str, options: Mapping[str, JsonScalar] | None, name: str | None) -> Path:
    data: JsonObject = {
        "name": name or Path(engine_path).stem,
        "engine_path": engine_path,
    }
    if options:
        data["options"] = json_serialize(dict(options))
    temp_dir = Path(tempfile.mkdtemp(prefix="arena_engine_"))
    temp_file = temp_dir / "engine.yaml"
    temp_file.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return temp_file


async def run_from_spec(spec: Mapping[str, JsonValue] | _RemotePairSpec) -> int:
    try:
        spec_obj = spec if isinstance(spec, _RemotePairSpec) else _RemotePairSpec.model_validate(spec)
    except ValidationError as exc:
        _emit_json_error(f"invalid spec JSON: {exc}")
        return 2

    progress_q: asyncio.Queue[tuple[int, int, str | None]] = asyncio.Queue()
    runner = GameRunner(progress_queue=progress_q)

    ctx: _RunContext = {
        "runner": runner,
        "streamer": None,
        "black_engine": None,
        "white_engine": None,
        "black_participant": None,
        "white_participant": None,
    }

    loop = asyncio.get_running_loop()

    def _on_signal(sig: signal.Signals) -> None:
        try:
            ctx["runner"].request_shutdown()
        except (RuntimeError, OSError) as exc:
            sys.stderr.write(f"[signal] failed to request shutdown: {exc}\n")
        streamer_task = ctx["streamer"]
        if streamer_task is not None:
            streamer_task.cancel()
        try:
            sys.stdout.write(
                json.dumps({"type": "error", "message": f"received signal: {sig.name}"}, ensure_ascii=False) + "\n"
            )
            sys.stdout.flush()
        except OSError as exc:
            sys.stderr.write(f"[signal] failed to emit error event: {exc}\n")

    def _install_handler(sig: signal.Signals) -> None:
        try:
            loop.add_signal_handler(sig, lambda: _on_signal(sig))
        except (NotImplementedError, RuntimeError, ValueError, OSError) as exc:
            sys.stderr.write(f"[signal] add_signal_handler failed for {str(sig)}: {exc}\n")

    for _sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        _install_handler(_sig)

    max_plies = spec_obj.max_plies
    if max_plies > 0:
        runner.adjudication_config = max_plies_only_adjudication(max_plies)

    try:
        black_limits = TimeControlLimits.model_validate(spec_obj.time_control.black)
        white_limits = TimeControlLimits.model_validate(spec_obj.time_control.white)
    except ValidationError as exc:
        _emit_json_error(f"invalid time_control: {exc}")
        return 2

    black_path = Path(spec_obj.black.engine_path)
    white_path = Path(spec_obj.white.engine_path)
    black_options = spec_obj.black.options
    white_options = spec_obj.white.options
    black_name = spec_obj.black.name
    white_name = spec_obj.white.name

    b_yaml = _write_temp_engine_yaml(str(black_path), black_options, black_name)
    w_yaml = _write_temp_engine_yaml(str(white_path), white_options, white_name)

    streamer = asyncio.create_task(_stream_progress(progress_q))
    ctx["streamer"] = streamer

    try:
        black_engine = await load_engine(str(b_yaml), engine_name=black_name)
        white_engine = await load_engine(str(w_yaml), engine_name=white_name)
        ctx["black_engine"] = black_engine
        ctx["white_engine"] = white_engine

        black_participant = EngineParticipant(
            black_engine,
            name_override=black_name,
            role=Color.BLACK,
        )
        white_participant = EngineParticipant(
            white_engine,
            name_override=white_name,
            role=Color.WHITE,
        )
        ctx["black_participant"] = black_participant
        ctx["white_participant"] = white_participant

        game_id = spec_obj.game_id or "game_remote"
        sfen = spec_obj.initial_sfen or "startpos"

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
        except TimeoutError:
            sys.stderr.write("[runner] streamer drain timed out; continuing\n")

        return 0
    except asyncio.CancelledError:
        _emit_json_error("cancelled by signal")
        return 1
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        tb = traceback.format_exc()
        message = f"remote game failed: {type(exc).__name__}: {exc}".rstrip()
        _emit_json_error(message, trace=tb[-4000:])
        return 1
    finally:
        streamer.cancel()

        shutdown_tasks: list[Awaitable[None]] = []
        participant = ctx["black_participant"]
        if participant is not None:
            shutdown_tasks.append(participant.shutdown())
        elif ctx["black_engine"] is not None:
            shutdown_tasks.append(ctx["black_engine"].close())

        participant = ctx["white_participant"]
        if participant is not None:
            shutdown_tasks.append(participant.shutdown())
        elif ctx["white_engine"] is not None:
            shutdown_tasks.append(ctx["white_engine"].close())

        if shutdown_tasks:
            try:
                await asyncio.gather(*shutdown_tasks, return_exceptions=True)
            except asyncio.CancelledError:
                sys.stderr.write("[cleanup] shutdown cancelled\n")


__all__ = ["register", "register_internal"]
