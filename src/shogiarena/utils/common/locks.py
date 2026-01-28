from __future__ import annotations

import os
import time
from pathlib import Path
from types import TracebackType
from typing import IO, Literal


class FileLockTimeout(TimeoutError):
    """Raised when acquiring a file lock exceeds the configured timeout."""


def _lock_file(handle: IO[bytes]) -> None:
    if os.name == "posix":
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    raise OSError(f"Unsupported platform for file locking: {os.name}")


def _unlock_file(handle: IO[bytes]) -> None:
    if os.name == "posix":
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    raise OSError(f"Unsupported platform for file locking: {os.name}")


class FileLock:
    """Simple cross-platform file lock with polling-based wait."""

    def __init__(self, path: Path, *, timeout: float | None = 600.0, poll_interval: float = 0.25) -> None:
        self.path = Path(path)
        self.timeout = timeout
        self.poll_interval = poll_interval if poll_interval > 0 else 0.1
        self._handle: IO[bytes] | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        deadline = start + self.timeout if self.timeout and self.timeout > 0 else None
        while True:
            handle = open(self.path, "a+b")
            try:
                _lock_file(handle)
            except OSError as exc:
                handle.close()
                if deadline is not None and time.monotonic() >= deadline:
                    raise FileLockTimeout(f"Timed out acquiring lock: {self.path}") from exc
                time.sleep(self.poll_interval)
                continue
            self._handle = handle
            try:
                handle.seek(0)
                handle.truncate()
                handle.write(f"{os.getpid()} {time.time():.3f}\n".encode("ascii", errors="ignore"))
                handle.flush()
                os.fsync(handle.fileno())
            except OSError:
                # Ignore metadata write failures; locking already succeeded.
                pass
            return

    def release(self) -> None:
        if not self._handle:
            return
        handle = self._handle
        self._handle = None
        try:
            _unlock_file(handle)
        finally:
            handle.close()

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        self.release()
        return False
