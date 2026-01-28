from __future__ import annotations


class SessionStopController:
    """Coordinator that tracks whether new games should be scheduled."""

    __slots__ = ("_stop_requested", "_reason")

    def __init__(self) -> None:
        self._stop_requested: bool = False
        self._reason: str | None = None

    def request_stop(self, *, reason: str | None = None) -> None:
        """Mark the session as stopped and optionally record a reason."""

        self._stop_requested = True
        if reason is not None:
            self._reason = reason

    def should_continue(self) -> bool:
        """Return ``True`` if scheduling should continue."""

        return not self._stop_requested

    @property
    def reason(self) -> str | None:
        """Return the recorded stop reason, if any."""

        return self._reason

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    def assert_running(self) -> None:
        """Raise ``RuntimeError`` if a stop has already been requested."""

        if self._stop_requested:
            detail = f" (reason={self._reason})" if self._reason else ""
            raise RuntimeError(f"Session stop already requested{detail}")
