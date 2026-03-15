from __future__ import annotations

import tempfile
from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.run_storage import FilesystemRunStorage


class TempRunStorage(FilesystemRunStorage):
    def __init__(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory(prefix="shogiarena-run-")
        super().__init__(Path(self._tempdir.name))
