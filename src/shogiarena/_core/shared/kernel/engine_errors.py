"""Shared engine communication errors.

Lives in the shared kernel because both the USI runtime that raises these and the game loop that
classifies them need the type, and the game loop must not depend on platform modules.
"""

from __future__ import annotations


class UsiHandshakeTimeoutError(TimeoutError):
    """Raised when a USI handshake exchange (``usi`` / ``isready``) does not complete in time.

    Distinct from a plain ``TimeoutError`` so callers can tell a handshake stall apart from an
    engine that failed to answer ``go``. Only the latter is a loss on time; a handshake stall
    happens before the engine is asked to think and must not be scored as one.
    """


__all__ = ["UsiHandshakeTimeoutError"]
