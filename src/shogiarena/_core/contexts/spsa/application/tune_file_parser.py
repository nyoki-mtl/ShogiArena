"""Internal helpers for parsing SPSA tune files."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .tune_block_models import Block, ParsedContent

_WHITESPACE = re.compile(r"[ \t]")

logger = logging.getLogger(__name__)


def _normalize_line(line: str) -> str:
    """Drop whitespace and newlines for loose string comparison."""

    without_ws = _WHITESPACE.sub("", line)
    return without_ws.replace("\n", "").replace("\r", "")


def _parse_tune_file(tune_file: str | Path) -> list[Block]:
    """Parse a tune file into raw blocks."""

    path = Path(tune_file)
    logger.debug("parse tune file, path = %s", path)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    blocks: list[Block] = []
    line_index = 0

    def read_next_block() -> Block | None:
        nonlocal line_index
        content: list[str] = []
        block_type = ""
        block_params: list[str] = []
        while line_index < len(lines):
            line = lines[line_index]
            if line.startswith("#"):
                if block_type:
                    break
                directive = line.split("//", 1)[0].strip()
                parts = directive.split()
                if not parts:
                    line_index += 1
                    continue
                block_type = parts[0][1:]
                block_params = parts[1:]
            else:
                content.append(line)
            line_index += 1
        return Block(block_type, block_params, content) if block_type else None

    while True:
        block = read_next_block()
        if block is None:
            break
        blocks.append(block)

    logger.debug("parsed %d blocks", len(blocks))
    return blocks


def _parse_content_block(block: Block, prefix: str) -> ParsedContent:
    """Replace inline @-parameters and collect metadata."""

    modified_block: list[str] = []
    removed_numbers: list[str] = []
    parameter_names: list[str] = []
    param_counter = 1

    def collect_and_remove(match: re.Match[str]) -> str:
        removed_numbers.append(match.group())
        return ""

    def replace_at(match: re.Match[str]) -> str:
        nonlocal param_counter
        suffix = match.group(1)
        if not suffix:
            suffix = str(param_counter)
            param_counter += 1
        prefix_part = f"{prefix}_" if prefix else "_"
        param_name = f"{prefix_part}{suffix}"
        parameter_names.append(param_name)
        return param_name

    for line in block.content:
        without_numbers = re.sub(r"-?\d+(?:\.\d+)?(?=@)", collect_and_remove, line)
        modified_block.append(re.sub(r"@([A-Za-z0-9]*)", replace_at, without_numbers))

    return ParsedContent(
        lines=modified_block,
        removed_numbers=removed_numbers,
        parameter_names=parameter_names,
    )


__all__: list[str] = []
