"""YaneuraOu option file の解析ヘルパー。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class YaneuraOuOptionEntry:
    """YaneuraOu option file の1行から解析した項目。"""

    name: str
    value: str
    line_no: int


def parse_yaneuraou_option_line(line: str, *, line_no: int = 0) -> YaneuraOuOptionEntry | None:
    """YaneuraOu ``engine_options.txt`` 互換の1行を解析する。"""

    stripped = line.split("//", 1)[0].strip()
    if not stripped:
        return None

    if "=" in stripped and not stripped.lower().startswith("option "):
        name, value = stripped.split("=", 1)
        normalized_name = name.strip()
        if not normalized_name:
            return None
        return YaneuraOuOptionEntry(name=normalized_name, value=value.strip(), line_no=line_no)

    tokens = stripped.split()
    if not tokens:
        return None

    if tokens[0].lower() != "option":
        return YaneuraOuOptionEntry(name=tokens[0], value=" ".join(tokens[1:]), line_no=line_no)

    return _parse_usi_option_line(tokens, line_no=line_no)


def parse_yaneuraou_option_lines(lines: Iterable[str]) -> tuple[YaneuraOuOptionEntry, ...]:
    """YaneuraOu option file の行列を解析し、コメント行や空行を無視する。"""

    entries: list[YaneuraOuOptionEntry] = []
    for index, line in enumerate(lines, start=1):
        parsed = parse_yaneuraou_option_line(line, line_no=index)
        if parsed is not None:
            entries.append(parsed)
    return tuple(entries)


def _parse_usi_option_line(tokens: list[str], *, line_no: int) -> YaneuraOuOptionEntry | None:
    lower_tokens = [token.lower() for token in tokens]
    if "name" not in lower_tokens:
        return None

    name_pos = lower_tokens.index("name") + 1
    end_pos = len(tokens)
    for marker in ("type", "default", "value", "min", "max", "var"):
        if marker in lower_tokens[name_pos:]:
            end_pos = min(end_pos, lower_tokens.index(marker, name_pos))

    name = " ".join(tokens[name_pos:end_pos]).strip()
    if not name:
        return None

    value = ""
    if "default" in lower_tokens:
        default_pos = lower_tokens.index("default") + 1
        if default_pos < len(tokens):
            value = tokens[default_pos]
    return YaneuraOuOptionEntry(name=name, value=value, line_no=line_no)


__all__ = [
    "YaneuraOuOptionEntry",
    "parse_yaneuraou_option_line",
    "parse_yaneuraou_option_lines",
]
