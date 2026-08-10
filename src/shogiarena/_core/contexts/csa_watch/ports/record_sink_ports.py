"""Contracts for the two places a finished CSA game is put."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from rsshogi.record import Record

CSA_EXPORT_VERSION = "3.0"
CSA_FILE_SUFFIX = ".csa"

# CSA-only facts have no arena column and must not grow the schema, so they ride
# in `metadata_attributes_json`. The precedent is `game_timeout_attribution`.
CSA_METADATA_PREFIX = "csa_"


class CsaRecordStoreRetryableError(RuntimeError):
    """A transient store failure that may be retried with backoff."""


class CsaRecordFileWriterPort(Protocol):
    """Writes one verified game as ``.csa`` text."""

    def write(self, record: Record, path: Path) -> int:
        """Return the number of bytes written."""
        ...


class CsaRecordStorePort(Protocol):
    """Persists one finished game into an arena run database."""

    def persist(self, record: Record) -> None: ...

    def close(self) -> None: ...


class CsaRecordStoreFactory(Protocol):
    """Opens a writable record store for one run database."""

    def __call__(self, db_path: Path) -> CsaRecordStorePort: ...


__all__ = [
    "CSA_EXPORT_VERSION",
    "CSA_FILE_SUFFIX",
    "CSA_METADATA_PREFIX",
    "CsaRecordStoreRetryableError",
    "CsaRecordFileWriterPort",
    "CsaRecordStoreFactory",
    "CsaRecordStorePort",
]
