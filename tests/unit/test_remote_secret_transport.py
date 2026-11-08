from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from shogiarena._core.contexts.instances.application.instance_models import Instance, InstanceConfig, InstanceType
from shogiarena._core.contexts.instances.application.ssh_transport import SshTransport
from shogiarena._core.platform.engine_provisioning.provisioning_ports import (
    RemoteSecretFileRequest,
)

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


def test_github_token_host_match_is_exact_or_subdomain() -> None:
    from shogiarena._core.interfaces.cli.config.repo_setup import _should_use_github_token

    assert _should_use_github_token("https://github.com/example/repo.git") is True
    assert _should_use_github_token("https://uploads.github.com/example/repo.git") is True
    assert _should_use_github_token("https://evilgithub.com/example/repo.git") is False


def test_settings_file_never_stores_the_github_token_and_resolves_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shogiarena._core.platform.settings.loader import write_settings_file
    from shogiarena._core.shared.kernel.settings_loading.settings_models import ArenaSettings

    settings_path = tmp_path / "settings.yaml"
    secret = "ghp_thismustnotbepersisted"
    monkeypatch.setenv("SHOGIARENA_GITHUB_TOKEN", secret)

    write_settings_file(
        settings_path,
        output_dir=tmp_path / "out",
        engine_dir=tmp_path / "engines",
        github_token_env="SHOGIARENA_GITHUB_TOKEN",
    )

    content = settings_path.read_text(encoding="utf-8")
    assert secret not in content
    assert "github_token:" not in content

    monkeypatch.setenv("CUSTOM_GH_TOKEN", "ghp_fromenv")
    settings = ArenaSettings(
        output_dir=tmp_path / "out",
        engine_dir=tmp_path / "engines",
        settings_path=settings_path,
        repos={},
        overlays={},
        openbench=None,
        github_token_env="CUSTOM_GH_TOKEN",
    )
    assert settings.github_token == "ghp_fromenv"
    monkeypatch.delenv("CUSTOM_GH_TOKEN")
    assert settings.github_token is None
