"""Helpers for applying CLI config overrides to TournamentRunConfig-like mappings."""

from __future__ import annotations

import re

from shogiarena._core.interfaces.boundaries.parsers.json_object import parse_yaml_value_boundary
from shogiarena._core.interfaces.cli.main import CliArgumentError
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue

_INDEX_RE = re.compile(r"^(?P<name>[^\[]+)?(?P<indices>(\[[0-9]+\])*)$")


def apply_override(target: JsonObject, path: str, value: JsonValue) -> None:
    tokens = _parse_path(path)
    if not tokens:
        raise CliArgumentError("override path must not be empty")

    current: JsonValue = target
    for idx, token in enumerate(tokens):
        last = idx == len(tokens) - 1
        next_token = tokens[idx + 1] if not last else None

        if isinstance(token, int):
            if not isinstance(current, list):
                raise CliArgumentError(f"override path expects list at '{_format_path(tokens[:idx])}'")
            _ensure_list_index(current, token, next_token)
            if last:
                current[token] = value
                return
            current = current[token]
            continue

        if not isinstance(current, dict):
            raise CliArgumentError(f"override path expects mapping at '{_format_path(tokens[:idx])}'")

        if last:
            current[token] = value
            return

        if token not in current or current[token] is None:
            current[token] = [] if isinstance(next_token, int) else {}
        else:
            _validate_container(current[token], next_token, path)
        current = current[token]


def apply_section_overrides(target: JsonObject, section: str, tokens: list[str]) -> None:
    if not tokens:
        return
    for raw in tokens:
        if "=" not in raw:
            raise CliArgumentError(f"invalid {section} token (expected KEY=VALUE): {raw}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise CliArgumentError(f"invalid {section} key: {raw}")
        parsed = _parse_scalar(value.strip(), label=f"{section} value for {key}")
        apply_override(target, f"{section}.{key}", parsed)


def parse_scalar(raw: str, *, label: str) -> JsonValue:
    return _parse_scalar(raw, label=label)


def _parse_scalar(raw: str, *, label: str) -> JsonValue:
    try:
        return parse_yaml_value_boundary(raw, label=label)
    except ValueError as exc:
        raise CliArgumentError(str(exc)) from exc


def _parse_path(path: str) -> list[str | int]:
    parts: list[str | int] = []
    for segment in path.split("."):
        segment = segment.strip()
        if not segment:
            raise CliArgumentError(f"invalid override path: {path}")
        match = _INDEX_RE.match(segment)
        if not match:
            raise CliArgumentError(f"invalid override path segment: {segment}")
        name = match.group("name")
        indices = match.group("indices") or ""
        if name:
            parts.append(name)
        if indices:
            for idx_raw in re.findall(r"\[([0-9]+)\]", indices):
                parts.append(int(idx_raw))
    return parts


def _ensure_list_index(items: list[JsonValue], index: int, next_token: str | int | None) -> None:
    if index < 0:
        raise CliArgumentError("list index must be >= 0")
    while len(items) <= index:
        items.append([] if isinstance(next_token, int) else {})


def _validate_container(container: JsonValue, next_token: str | int | None, path: str) -> None:
    if next_token is None:
        return
    if isinstance(next_token, int):
        if not isinstance(container, list):
            raise CliArgumentError(f"override path expects list for '{path}'")
    elif not isinstance(container, dict):
        raise CliArgumentError(f"override path expects mapping for '{path}'")


def _format_path(tokens: list[str | int]) -> str:
    if not tokens:
        return ""
    out = []
    for token in tokens:
        if isinstance(token, int):
            out.append(f"[{token}]")
        else:
            if out:
                out.append(".")
            out.append(token)
    return "".join(out)
