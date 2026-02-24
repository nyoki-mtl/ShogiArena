"""Helpers for applying CLI config overrides to TournamentRunConfig-like mappings."""

from __future__ import annotations

import re
from typing import Any

import yaml

from shogiarena.cli.errors import CliArgumentError

_INDEX_RE = re.compile(r"^(?P<name>[^\[]+)?(?P<indices>(\[[0-9]+\])*)$")


def apply_override(target: dict[str, Any], path: str, value: Any) -> None:
    tokens = _parse_path(path)
    if not tokens:
        raise CliArgumentError("override path must not be empty")

    current: Any = target
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


def apply_section_overrides(target: dict[str, Any], section: str, tokens: list[str]) -> None:
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


def _parse_override(raw: str) -> tuple[str, Any]:
    if "=" not in raw:
        raise CliArgumentError(f"invalid override (expected PATH=VALUE): {raw}")
    path, value = raw.split("=", 1)
    path = path.strip()
    if not path:
        raise CliArgumentError(f"invalid override path: {raw}")
    parsed = _parse_scalar(value.strip(), label=f"override value for {path}")
    return path, parsed


def parse_scalar(raw: str, *, label: str) -> Any:
    return _parse_scalar(raw, label=label)


def _parse_scalar(raw: str, *, label: str) -> Any:
    if raw == "":
        raise CliArgumentError(f"{label} must not be empty")
    try:
        value = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise CliArgumentError(f"failed to parse {label}: {exc}") from exc
    return value


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


def _ensure_list_index(items: list[Any], index: int, next_token: Any | None) -> None:
    if index < 0:
        raise CliArgumentError("list index must be >= 0")
    while len(items) <= index:
        items.append([] if isinstance(next_token, int) else {})


def _validate_container(container: Any, next_token: Any | None, path: str) -> None:
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
