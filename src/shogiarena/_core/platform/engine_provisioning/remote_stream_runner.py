"""Remote runner stream handling and command composition."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import shlex
from collections.abc import AsyncIterator, Callable, Sequence

from pydantic import BaseModel, ConfigDict, ValidationError

from shogiarena._core.platform.engine_provisioning.provisioning_ports import (
    NamedInstancePort,
    RemoteSecretCommandFactory,
    RemoteSecretFileRequest,
    SshSecretFileTransportPort,
    SshStreamTransportPort,
)
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.serialization import json_serialize


class _RemoteEventPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str | None = None
    message: str | None = None


class RemoteStreamConsumer:
    """Consume JSON line streams from the remote runner."""

    def __init__(
        self,
        instance: NamedInstancePort,
        transport: SshStreamTransportPort,
        logger: logging.Logger,
    ) -> None:
        self._instance = instance
        self._transport = transport
        self._logger = logger

    async def collect(
        self,
        command: str,
        *,
        timeout: float | None,
        on_event: Callable[[JsonObject], None] | None,
    ) -> list[JsonObject]:
        line_iter = self._transport.run_stream_lines(command)
        return await self._collect_from_lines(line_iter, timeout=timeout, on_event=on_event)

    async def collect_with_secret_files(
        self,
        command_factory: RemoteSecretCommandFactory,
        secret_files: Sequence[RemoteSecretFileRequest],
        *,
        timeout: float | None,
        on_event: Callable[[JsonObject], None] | None,
    ) -> list[JsonObject]:
        secret_transport = self._secret_transport()
        line_iter = secret_transport.run_stream_lines_with_secret_files(command_factory, secret_files)
        return await self._collect_from_lines(line_iter, timeout=timeout, on_event=on_event)

    async def _collect_from_lines(
        self,
        line_iter: AsyncIterator[str],
        *,
        timeout: float | None,
        on_event: Callable[[JsonObject], None] | None,
    ) -> list[JsonObject]:
        events: list[JsonObject] = []
        exit_code: int | None = None
        last_error: str | None = None
        last_text: str | None = None

        async def drain() -> None:
            nonlocal exit_code, last_error, last_text
            async for raw_line in line_iter:
                line = raw_line.strip()
                if line.startswith("__REMOTE_EXIT_RC:"):
                    try:
                        exit_code = int(line.split(":", 1)[1])
                    except ValueError as exc:
                        raise RuntimeError("Malformed exit code sentinel from remote runner") from exc
                    continue
                if line.startswith("{"):
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError as exc:
                        context = line[:200]
                        message = (
                            f"Malformed JSON from remote {self._instance.name}: {exc.msg} "
                            f"(pos {exc.pos}); context={context!r}"
                        )
                        self._logger.error(message)
                        raise RuntimeError(message) from exc
                    if isinstance(payload, dict):
                        try:
                            normalized_payload = _RemoteEventPayload.model_validate(payload).model_dump(mode="python")
                        except ValidationError:
                            normalized_payload = payload
                        parsed_payload: JsonObject = {
                            str(key): json_serialize(value) for key, value in normalized_payload.items()
                        }
                        events.append(parsed_payload)
                        event_type = parsed_payload.get("type")
                        event_message = parsed_payload.get("message")
                        if event_type == "error" and isinstance(event_message, str):
                            last_error = event_message[:500]
                        if on_event is not None:
                            on_event(parsed_payload)
                    continue
                if line:
                    last_text = line[:500]

        try:
            if timeout is None:
                await drain()
            else:
                await asyncio.wait_for(drain(), timeout)
        except TimeoutError as exc:
            raise TimeoutError("remote pair timed out") from exc
        finally:
            await self._close_iterator(line_iter)

        if exit_code is None:
            raise RuntimeError("remote pair terminated without exit code sentinel")
        if exit_code != 0:
            message = f"remote pair failed with exit code {exit_code}"
            if last_error:
                message += f": {last_error}"
            elif last_text:
                message += f": {last_text}"
            raise RuntimeError(message)
        return events

    def _secret_transport(self) -> SshSecretFileTransportPort:
        if not isinstance(self._transport, SshSecretFileTransportPort):
            raise RuntimeError("SSH stream transport does not support secret file handoff.")
        return self._transport

    @staticmethod
    async def _close_iterator(iterator: AsyncIterator[str]) -> None:
        closer = getattr(iterator, "aclose", None)
        if closer is None:
            return
        close_result = closer()
        if inspect.isawaitable(close_result):
            await close_result


def build_remote_runner_command(
    base_abs: str, remote_spec: str, spec_json: str, *, github_token_file: str | None = None
) -> str:
    """Construct the shell command responsible for launching the remote runner."""
    script_lines = [
        "set -e",
        'export PATH="$HOME/.local/bin:$PATH"',
        f"BASE={shlex.quote(base_abs)}",
        f"SPEC={shlex.quote(remote_spec)}",
        'mkdir -p "$BASE/.tmp"',
        "cat > \"$SPEC\" <<'__ARENA_SPEC_JSON__'",
        spec_json,
        "__ARENA_SPEC_JSON__",
    ]

    if github_token_file:
        script_lines.extend(
            [
                f"GITHUB_TOKEN_FILE={shlex.quote(github_token_file)}",
                'GITHUB_TOKEN="$(cat "$GITHUB_TOKEN_FILE")"',
                "export GITHUB_TOKEN",
            ]
        )

    script_lines.extend(
        [
            'rc=0; uv --directory "$BASE" run -- '
            'shogiarena _internal remote-run-pair --spec-file "$SPEC" 2>&1 || rc=$?',
            "echo __REMOTE_EXIT_RC:$rc",
        ]
    )
    script = "\n".join(script_lines)
    return "bash -lc " + shlex.quote(script)


__all__ = ["RemoteStreamConsumer", "build_remote_runner_command"]
