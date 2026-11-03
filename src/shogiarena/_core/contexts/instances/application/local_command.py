"""Own health-command processes and pipe callbacks through cancellation."""

from __future__ import annotations

import asyncio
import os
import signal

from shogiarena._core.shared.kernel.async_completion import complete_before_cancelling

from .local_process_protocol import LocalProcessProtocol


async def run_local_command(command: list[str], *, timeout: float) -> tuple[int, bytes, bytes]:
    """Collect a local command, terminating its owned process group on interruption."""
    loop = asyncio.get_running_loop()
    protocol = LocalProcessProtocol(loop, limit=64 * 1024)
    spawn = asyncio.create_task(
        loop.subprocess_exec(
            lambda: protocol,
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
    )

    async def close(*, interrupted: bool) -> None:
        # A cancellation racing with spawn must not discard its returned handle.
        try:
            transport, _ = await spawn
        except (Exception, asyncio.CancelledError):
            # subprocess_exec owns cleanup when construction itself fails.
            return
        try:
            if interrupted:
                try:
                    if os.name == "posix":
                        # This group was created by start_new_session, and may
                        # outlive an exited shell while descendants hold pipes.
                        os.killpg(transport.get_pid(), signal.SIGKILL)
                    elif transport.get_returncode() is None:
                        transport.kill()
                except ProcessLookupError:
                    pass
        finally:
            transport.close()
            await protocol.disconnected

    try:
        transport, _ = await asyncio.shield(spawn)
        process = asyncio.subprocess.Process(transport, protocol.streams, loop)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        assert process.returncode is not None
        result = process.returncode, stdout, stderr
    except BaseException:
        try:
            await complete_before_cancelling(close(interrupted=True))
        except asyncio.CancelledError:
            # Retain the original cancellation/message or command failure.
            pass
        raise
    await complete_before_cancelling(close(interrupted=False))
    return result
