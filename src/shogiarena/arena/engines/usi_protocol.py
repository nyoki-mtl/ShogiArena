"""Pure USI protocol parsing helpers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from shogiarena.arena.engines.usi_types import UsiThinkPV, UsiThinkResult


@dataclass(slots=True, frozen=True)
class UsiIdField:
    """Parsed ``id <key> <value>`` line."""

    key: str
    value: str


@dataclass(slots=True)
class UsiOption:
    """Structured representation of a USI ``option`` declaration."""

    name: str
    option_type: str
    default: str | None = None
    current: str | None = None
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()


class UsiProtocolParser:
    """Stateless helpers for parsing USI protocol output."""

    @staticmethod
    def parse_id(line: str) -> UsiIdField | None:
        """Parse an ``id`` line; return None for unrelated lines."""
        if not line.startswith("id "):
            return None
        parts = line.split(maxsplit=2)
        if len(parts) != 3:
            raise ValueError(f"Invalid id line (expected 'id <key> <value>'): {line}")
        key, value = parts[1], parts[2].strip()
        if not key:
            raise ValueError(f"Invalid id line; missing key: {line}")
        if not value:
            raise ValueError(f"Invalid id line; missing value for key '{key}': {line}")
        return UsiIdField(key=key, value=value)

    @staticmethod
    def parse_option(line: str) -> UsiOption | None:
        """Parse an ``option`` line; return None for unrelated lines."""
        if not line.startswith("option "):
            return None
        parts = line.split()
        name_tokens, option_type, cursor = UsiProtocolParser._parse_option_header(parts, line)
        default_value, min_value, max_value, variants = UsiProtocolParser._parse_option_attributes(parts, cursor, line)
        option_name = " ".join(name_tokens)

        return UsiOption(
            name=option_name,
            option_type=option_type,
            default=default_value,
            current=default_value,
            minimum=min_value,
            maximum=max_value,
            choices=tuple(variants),
        )

    @staticmethod
    def _parse_option_header(parts: list[str], line: str) -> tuple[list[str], str, int]:
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

    @staticmethod
    def _parse_option_attributes(
        parts: list[str],
        start_index: int,
        line: str,
    ) -> tuple[str | None, int | None, int | None, list[str]]:
        default_value: str | None = None
        min_value: int | None = None
        max_value: int | None = None
        variants: list[str] = []

        i = start_index
        while i < len(parts):
            token = parts[i]
            if token == "default":
                if i + 1 >= len(parts):
                    raise ValueError(f"Missing default value in line: {line}")
                default_value = parts[i + 1]
                i += 2
                continue
            if token == "min":
                if i + 1 >= len(parts):
                    raise ValueError(f"Missing min value in line: {line}")
                try:
                    min_value = int(parts[i + 1])
                except ValueError as exc:
                    raise ValueError(f"Invalid min value in line: {line}") from exc
                i += 2
                continue
            if token == "max":
                if i + 1 >= len(parts):
                    raise ValueError(f"Missing max value in line: {line}")
                try:
                    max_value = int(parts[i + 1])
                except ValueError as exc:
                    raise ValueError(f"Invalid max value in line: {line}") from exc
                i += 2
                continue
            if token == "var":
                if i + 1 >= len(parts):
                    raise ValueError(f"Missing var value in line: {line}")
                variants.append(parts[i + 1])
                i += 2
                continue
            i += 1

        return default_value, min_value, max_value, variants

    @staticmethod
    def parse_info(line: str) -> UsiThinkPV | None:
        """Parse an ``info`` line using ``UsiThinkPV`` utilities."""
        return UsiThinkPV.from_info_string(line)

    @staticmethod
    def parse_bestmove(line: str, *, pvs: Iterable[UsiThinkPV] | None = None) -> UsiThinkResult | None:
        """Parse a ``bestmove`` line and attach provided PV snapshots."""
        if not line.startswith("bestmove"):
            return None
        result = UsiThinkResult.from_string(line)
        if pvs:
            result.pvs.extend(pvs)
        return result
