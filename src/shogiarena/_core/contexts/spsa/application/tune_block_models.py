from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Block:
    """A chunk parsed from a tune file (#set, #context, #add)."""

    type: str
    params: list[str]
    content: list[str]


@dataclass(slots=True)
class TuneBlock:
    """Collected directives that describe how to patch a target file."""

    set_directives: dict[str, str]
    template_block: Block
    match_block: Block
    add_blocks: list[Block] = field(default_factory=list)


@dataclass(slots=True)
class ParsedContent:
    """Parsed context block with the generated parameter names."""

    lines: list[str]
    removed_numbers: list[str]
    parameter_names: list[str]


__all__ = ["Block", "ParsedContent", "TuneBlock"]
