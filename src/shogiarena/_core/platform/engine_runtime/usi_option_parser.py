"""Parsing helpers for USI `option` lines."""

from __future__ import annotations


def parse_option_header(parts: list[str], line: str) -> tuple[list[str], str, int]:
    if len(parts) < 4 or parts[1] != "name":
        raise ValueError(f"Invalid option line: {line}")
    try:
        type_index = parts.index("type", 2)
    except ValueError as exc:
        raise ValueError(f"Missing option type in line: {line}") from exc

    name_tokens = parts[2:type_index]
    if not name_tokens:
        raise ValueError(f"Option name missing in line: {line}")
    if type_index + 1 >= len(parts):
        raise ValueError(f"Option type missing in line: {line}")

    option_type = parts[type_index + 1]
    return name_tokens, option_type, type_index + 2


def parse_option_attributes(
    parts: list[str],
    start_index: int,
    line: str,
) -> tuple[str | None, int | None, int | None, list[str]]:
    default_value: str | None = None
    min_value: int | None = None
    max_value: int | None = None
    variants: list[str] = []

    index = start_index
    while index < len(parts):
        token = parts[index]
        if token == "default":
            if index + 1 >= len(parts):
                raise ValueError(f"Missing default value in line: {line}")
            default_value = parts[index + 1]
            index += 2
            continue
        if token == "min":
            if index + 1 >= len(parts):
                raise ValueError(f"Missing min value in line: {line}")
            try:
                min_value = int(parts[index + 1])
            except ValueError as exc:
                raise ValueError(f"Invalid min value in line: {line}") from exc
            index += 2
            continue
        if token == "max":
            if index + 1 >= len(parts):
                raise ValueError(f"Missing max value in line: {line}")
            try:
                max_value = int(parts[index + 1])
            except ValueError as exc:
                raise ValueError(f"Invalid max value in line: {line}") from exc
            index += 2
            continue
        if token == "var":
            if index + 1 >= len(parts):
                raise ValueError(f"Missing var value in line: {line}")
            variants.append(parts[index + 1])
            index += 2
            continue
        index += 1

    return default_value, min_value, max_value, variants


__all__ = ["parse_option_attributes", "parse_option_header"]
