from __future__ import annotations

from collections.abc import Iterable

from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str


def compute_instance_defaults(entries: Iterable[tuple[str, str | None]]) -> dict[str, str | None]:
    defaults: dict[str, str | None] = {}
    for name, raw_instance_id in entries:
        if name in defaults:
            continue
        instance_id = coerce_str(raw_instance_id)
        defaults[name] = instance_id if instance_id is not None else "local"
    return defaults


__all__ = ["compute_instance_defaults"]
