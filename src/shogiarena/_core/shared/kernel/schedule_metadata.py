"""Schedule metadata codec for arena game records."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from json import JSONDecodeError

from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_or_none
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.serialization import json_serialize

logger = logging.getLogger(__name__)

ARENA_SCHEDULE_ATTRIBUTE = "_arena_schedule"


def serialize_schedule_metadata(metadata: Mapping[str, object]) -> str:
    """Serialize schedule metadata into a record metadata attribute string."""

    payload = {str(key): json_serialize(value) for key, value in metadata.items()}
    return json.dumps(payload, ensure_ascii=False)


def deserialize_schedule_metadata(raw: object) -> JsonObject | None:
    """Decode schedule metadata from a record metadata attribute value."""

    if isinstance(raw, Mapping):
        return coerce_json_object_or_none(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        decoded = json.loads(raw)
    except JSONDecodeError:
        logger.debug("Skipping invalid schedule metadata JSON")
        return None
    return coerce_json_object_or_none(decoded)


def attach_schedule_metadata(*, game_record: object, schedule_metadata: Mapping[str, object] | None) -> None:
    """Attach schedule metadata to a game record when available."""

    if not schedule_metadata:
        return
    setter = getattr(game_record, "set_metadata_attribute", None)
    if not callable(setter):
        return
    setter(ARENA_SCHEDULE_ATTRIBUTE, serialize_schedule_metadata(schedule_metadata))


def extract_schedule_metadata(record: object) -> JsonObject | None:
    """Extract schedule metadata from a game record."""

    metadata = getattr(record, "metadata", None)
    attributes = getattr(metadata, "attributes", None) if metadata is not None else None
    if not isinstance(attributes, Mapping):
        return None
    return deserialize_schedule_metadata(attributes.get(ARENA_SCHEDULE_ATTRIBUTE))


__all__ = [
    "ARENA_SCHEDULE_ATTRIBUTE",
    "attach_schedule_metadata",
    "deserialize_schedule_metadata",
    "extract_schedule_metadata",
    "serialize_schedule_metadata",
]
