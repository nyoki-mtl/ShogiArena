from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from shogiarena._core.contexts.instances.application.instance_models import Instance, InstanceConfig, InstanceType
from shogiarena._core.contexts.instances.application.ssh_transport import SshTransport
from shogiarena._core.platform.engine_provisioning.provisioning_ports import (
    RemoteSecretCommandFactory,
    RemoteSecretFileHandle,
    RemoteSecretFileRequest,
)
from shogiarena._core.platform.engine_provisioning.remote_repo_manager import RemoteRepoSpec, RemoteRepoSynchronizer
from shogiarena._core.platform.engine_provisioning.remote_stream_runner import (
    RemoteStreamConsumer,
    build_remote_runner_command,
)
from shogiarena._core.shared.kernel.json_types import JsonObject

TOKEN = "ghp_remote_secret_value"


def _make_ssh_instance() -> Instance:
    return Instance(
        config=InstanceConfig(
            name="remote",
            type=InstanceType.SSH,
            engine_dir="",
            host="example.invalid",
            user="arena",
        )
    )


class _LifecycleTransport(SshTransport):
    def __init__(self) -> None:
        super().__init__(_make_ssh_instance())
        self.commands: list[str] = []
        self.uploads_by_path: dict[str, bytes] = {}
        self.cleanup_count = 0
        self.command_result: tuple[int, str, str] = (0, "", "")
        self.should_block_command = False
        self.command_started = asyncio.Event()
        self.command_release = asyncio.Event()
        self.stream_closed = False

    async def connect(self) -> None:
        return

    async def close(self) -> None:
        return

    async def run(
        self,
        command: str,
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        self.commands.append(command)
        if "mktemp -d" in command:
            return 0, "/tmp/shogiarena-secret.test", ""
        if command.startswith("chmod "):
            return 0, "", ""
        if command.startswith("rm -rf -- "):
            self.cleanup_count += 1
            return 0, "", ""
        self.command_started.set()
        if self.should_block_command:
            await self.command_release.wait()
        return self.command_result

    def run_stream_lines(self, command: str, *, env: dict[str, str] | None = None) -> AsyncIterator[str]:
        self.commands.append(command)

        async def lines() -> AsyncIterator[str]:
            try:
                yield "first"
                yield "__REMOTE_EXIT_RC:0"
            finally:
                self.stream_closed = True

        return lines()

    async def mkdir(self, path: str, *, is_existing_ok: bool = True) -> None:
        return

    async def put_file(self, local: Path, remote: str) -> None:
        self.uploads_by_path[remote] = local.read_bytes()


class _RepoSecretTransport:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.secret_commands: list[str] = []
        self.secret_files: list[RemoteSecretFileRequest] = []

    async def run(
        self,
        command: str,
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        self.commands.append(command)
        if command == "test -e /repo":
            return 1, "", ""
        return 0, "", ""

    async def run_with_secret_files(
        self,
        command_factory: RemoteSecretCommandFactory,
        secret_files: Sequence[RemoteSecretFileRequest],
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        self.secret_files.extend(secret_files)
        handles = {
            request.file_id: RemoteSecretFileHandle(
                file_id=request.file_id,
                remote_path=f"/tmp/secret/{request.file_id}",
            )
            for request in secret_files
        }
        self.secret_commands.append(command_factory(handles))
        return 0, "", ""

    def run_stream_lines_with_secret_files(
        self,
        command_factory: RemoteSecretCommandFactory,
        secret_files: Sequence[RemoteSecretFileRequest],
        *,
        env: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        async def lines() -> AsyncIterator[str]:
            yield "__REMOTE_EXIT_RC:0"

        return lines()


class _StreamSecretTransport:
    def __init__(self) -> None:
        self.command: str | None = None

    def run_stream_lines(self, command: str, *, env: dict[str, str] | None = None) -> AsyncIterator[str]:
        raise AssertionError("plain stream API should not be used")

    async def run_with_secret_files(
        self,
        command_factory: RemoteSecretCommandFactory,
        secret_files: Sequence[RemoteSecretFileRequest],
        *,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        raise AssertionError("command API should not be used")

    def run_stream_lines_with_secret_files(
        self,
        command_factory: RemoteSecretCommandFactory,
        secret_files: Sequence[RemoteSecretFileRequest],
        *,
        env: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        handles = {
            request.file_id: RemoteSecretFileHandle(
                file_id=request.file_id,
                remote_path=f"/tmp/secret/{request.file_id}",
            )
            for request in secret_files
        }
        self.command = command_factory(handles)

        async def lines() -> AsyncIterator[str]:
            yield '{"type":"ok"}'
            yield "__REMOTE_EXIT_RC:0"

        return lines()


@pytest.mark.asyncio
async def test_secret_transport_cleans_up_after_nonzero_command() -> None:
    transport = _LifecycleTransport()
    transport.command_result = (7, "", "boom")

    rc, _out, err = await transport.run_with_secret_files(
        lambda handles: f"cat {handles['github_token'].remote_path}",
        [RemoteSecretFileRequest(file_id="github_token", payload=TOKEN.encode("utf-8"))],
    )

    assert rc == 7
    assert err == "boom"
    assert transport.cleanup_count == 1
    assert transport.uploads_by_path["/tmp/shogiarena-secret.test/github_token"] == TOKEN.encode("utf-8")
    assert all(TOKEN not in command for command in transport.commands)


@pytest.mark.asyncio
async def test_secret_transport_cleans_up_after_cancellation() -> None:
    transport = _LifecycleTransport()
    transport.should_block_command = True
    task = asyncio.create_task(
        transport.run_with_secret_files(
            lambda handles: f"cat {handles['github_token'].remote_path}",
            [RemoteSecretFileRequest(file_id="github_token", payload=TOKEN.encode("utf-8"))],
        )
    )
    await asyncio.wait_for(transport.command_started.wait(), timeout=1.0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert transport.cleanup_count == 1
    assert all(TOKEN not in command for command in transport.commands)


@pytest.mark.asyncio
async def test_secret_stream_transport_cleans_up_on_early_close() -> None:
    transport = _LifecycleTransport()
    iterator = transport.run_stream_lines_with_secret_files(
        lambda handles: f"cat {handles['github_token'].remote_path}",
        [RemoteSecretFileRequest(file_id="github_token", payload=TOKEN.encode("utf-8"))],
    )

    assert await anext(iterator) == "first"
    await iterator.aclose()

    assert transport.stream_closed is True
    assert transport.cleanup_count == 1
    assert all(TOKEN not in command for command in transport.commands)


@pytest.mark.asyncio
async def test_remote_repo_github_token_uses_secret_askpass_without_command_leak() -> None:
    transport = _RepoSecretTransport()
    synchronizer = RemoteRepoSynchronizer(
        transport,
        RemoteRepoSpec(base="/repo", url="https://github.com/example/private.git", ref="main"),
    )

    await synchronizer._clone_repository("/repo", TOKEN)
    await synchronizer._fetch_updates("/repo", TOKEN)

    captured_commands = [*transport.commands, *transport.secret_commands]
    assert all(TOKEN not in command for command in captured_commands)
    assert all("Authorization: Bearer" not in command for command in captured_commands)
    assert all("http.extraHeader" not in command for command in captured_commands)
    assert all("x-access-token:" not in command for command in captured_commands)
    assert any("GIT_ASKPASS=/tmp/secret/git_askpass" in command for command in transport.secret_commands)
    assert any("SHOGIARENA_GIT_TOKEN_FILE=/tmp/secret/github_token" in command for command in transport.secret_commands)
    assert {request.file_id for request in transport.secret_files} == {"github_token", "git_askpass"}
    assert any(request.file_id == "git_askpass" and request.mode == 0o700 for request in transport.secret_files)
    assert any(
        request.file_id == "github_token" and request.payload == TOKEN.encode("utf-8")
        for request in transport.secret_files
    )


@pytest.mark.asyncio
async def test_remote_repo_non_github_remote_does_not_use_secret_transport() -> None:
    transport = _RepoSecretTransport()
    synchronizer = RemoteRepoSynchronizer(
        transport,
        RemoteRepoSpec(base="/repo", url="https://evilgithub.com/example/repo.git", ref="main"),
    )

    await synchronizer._fetch_updates("/repo", TOKEN)

    assert transport.secret_commands == []
    assert transport.commands == [
        "git -C /repo fetch --prune https://evilgithub.com/example/repo.git +refs/heads/*:refs/remotes/origin/*"
    ]


def test_remote_runner_command_uses_token_file_path_not_token_value() -> None:
    command = build_remote_runner_command(
        "/repo",
        "/tmp/spec.json",
        "{}",
        github_token_file="/tmp/secret/github_token",
    )

    assert TOKEN not in command
    assert "/tmp/secret/github_token" in command
    assert 'GITHUB_TOKEN="$(cat "$GITHUB_TOKEN_FILE")' in command
    assert "export GITHUB_TOKEN=" not in command


@pytest.mark.asyncio
async def test_remote_stream_consumer_uses_secret_stream_transport_for_token_file() -> None:
    transport = _StreamSecretTransport()
    consumer = RemoteStreamConsumer(SimpleNamespace(name="remote"), transport, logging.getLogger(__name__))

    def build_command(handles: Mapping[str, RemoteSecretFileHandle]) -> str:
        return build_remote_runner_command(
            "/repo",
            "/tmp/spec.json",
            "{}",
            github_token_file=handles["github_token"].remote_path,
        )

    events: list[JsonObject] = await consumer.collect_with_secret_files(
        build_command,
        [RemoteSecretFileRequest(file_id="github_token", payload=TOKEN.encode("utf-8"))],
        timeout=None,
        on_event=None,
    )

    assert events == [{"type": "ok", "message": None}]
    assert transport.command is not None
    assert TOKEN not in transport.command
