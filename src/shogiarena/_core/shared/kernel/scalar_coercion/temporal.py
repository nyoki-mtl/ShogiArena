from __future__ import annotations

import math
from datetime import UTC, datetime


def coerce_timestamp_ms(value: object | None) -> int | None:
    """Coerce value into a millisecond timestamp."""

    match value:
        case bool():
            return None
        case int() as number:
            return number
        case float() as fvalue:
            return int(fvalue) if math.isfinite(fvalue) else None
        case str() as svalue:
            raw = svalue.strip()
            if not raw:
                return None
            try:
                fvalue = float(raw)
                if not math.isfinite(fvalue):
                    return None
                return int(fvalue)
            except (ValueError, OverflowError):
                try:
                    normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
                    parsed = datetime.fromisoformat(normalized)
                    return int(parsed.timestamp() * 1000)
                except ValueError:
                    return None
        case _:
            return None


def timestamp_to_iso(value: object | None) -> str | None:
    """Convert millisecond timestamp-like values into ISO string."""

    match value:
        case bool():
            return None
        case int() | float() as number:
            try:
                return datetime.fromtimestamp(float(number) / 1000.0, tz=UTC).isoformat()
            except (OSError, OverflowError, ValueError):
                return None
        case _:
            return None


def datetime_to_iso(value: datetime | object | None) -> str | None:
    """Normalize ``datetime | str`` into ISO string."""

    match value:
        case datetime() as dt_value:
            return dt_value.isoformat()
        case str() as svalue:
            stripped = svalue.strip()
            if not stripped:
                return None
            try:
                datetime.fromisoformat(stripped)
            except ValueError:
                return None
            return stripped
        case _:
            return None


def coerce_iso_datetime(value: object | None) -> datetime | None:
    """Convert ISO-8601 datetime-like value to ``datetime``."""

    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    normalized = f"{stripped[:-1]}+00:00" if stripped.endswith("Z") else stripped
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


__all__ = ["coerce_iso_datetime", "coerce_timestamp_ms", "datetime_to_iso", "timestamp_to_iso"]
