"""Persistence helper functions used by the instances API."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from shogiarena._core.contexts.instances.application.entrypoints import Instance
from shogiarena._core.platform.db.store.entities import (
    InstanceSpec,
)
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory
from shogiarena._core.shared.kernel.json_types import JsonObject


def upsert_instance_spec(
    db_path: Path,
    *,
    instance: object,
) -> None:
    """Persist current instance spec/metrics in ``instance_spec``."""

    if not isinstance(instance, Instance):
        raise TypeError("instance must be an Instance")

    metrics = instance.metrics
    shogidb = SQLiteShogiDBFactory(db_path).create()
    try:
        shogidb.create_tables()
        session = shogidb.session
        existing = session.execute(
            select(InstanceSpec).where(InstanceSpec.instance_id == instance.name)
        ).scalar_one_or_none()
        entity = existing if existing is not None else InstanceSpec(instance_id=instance.name)
        if existing is None:
            session.add(entity)

        entity.display_name = instance.name
        entity.host_label = instance.config.host
        entity.cpu_model = metrics.cpu_model
        entity.cpu_arch = None
        entity.cpu_cores = metrics.cpu_count
        entity.cpu_threads = metrics.cpu_count
        entity.memory_total_mb = metrics.mem_total_mb
        entity.os_info = None
        entity.gpu_model = None
        entity.gpu_vendor = None
        entity.gpu_vram_mb = None
        entity.gpu_count = None
        entity.instance_type = instance.type.value
        tags = list(instance.config.tags or [])
        entity.tags = tags if tags else None
        extra: JsonObject = {
            "slots": instance.config.slots,
            "reachable": metrics.is_reachable,
            "engine_dir": instance.config.engine_dir,
            "project_root": instance.config.project_root,
            "is_local": instance.is_local,
            "is_ssh": instance.is_ssh,
        }
        entity.extra = extra
        session.commit()
    finally:
        shogidb.close_db()


__all__ = ["upsert_instance_spec"]
