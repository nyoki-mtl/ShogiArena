from __future__ import annotations

from shogiarena.records.formats.base import RecordCodec
from shogiarena.records.formats.registry import (
    get_codec,
    register_codec,
    registry,
    supported_formats,
)

from . import csa as _csa_module  # noqa: F401 (registration side-effect)
from . import kif as _kif_module  # noqa: F401 (registration side-effect)
from . import psv as _psv_module  # noqa: F401 (registration side-effect)
from . import sbinpack as _sbinpack_module  # noqa: F401 (registration side-effect)

__all__ = [
    "RecordCodec",
    "get_codec",
    "register_codec",
    "registry",
    "supported_formats",
]
