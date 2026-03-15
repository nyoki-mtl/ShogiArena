"""Run result persistence adapters."""

from __future__ import annotations

from shogiarena._core.contexts.game_session.ports.result_store import (
    PersistedRunResultPort,
    ResultStorePort,
)
from shogiarena._core.contexts.game_session.ports.run_storage import RunStoragePort

from .payload_codec import serialize_run_result_payload


class RunStorageResultStore(ResultStorePort):
    """RunStorage をバックエンドとする ResultStorePort 実装。"""

    def __init__(self, storage: RunStoragePort) -> None:
        self._storage = storage

    def save_result(self, result: PersistedRunResultPort) -> None:
        self._storage.write_json(f"results/{result.run_id}.json", serialize_run_result_payload(result))


__all__ = ["RunStorageResultStore"]
