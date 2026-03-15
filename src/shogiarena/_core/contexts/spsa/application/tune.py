"""Helpers for reading SPSA tune files and applying parameter updates."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .param_io import ParamEntry, read_params, write_params
from .tune_block_models import Block, ParsedContent, TuneBlock
from .tune_file_parser import _normalize_line, _parse_content_block, _parse_tune_file

logger = logging.getLogger(__name__)


def read_tune_file(tune_file: str | Path) -> list[TuneBlock]:
    """Convert the raw blocks into structured tune instructions."""

    blocks = _parse_tune_file(tune_file)
    logger.debug("read tune file, path = %s", tune_file)

    set_directives: dict[str, str] = {}
    current_context = None
    pending_add_blocks = []
    result: list[TuneBlock] = []

    def flush_current() -> None:
        nonlocal current_context, pending_add_blocks
        if current_context is None:
            return
        sanitized_lines = [re.sub(r"@[0-9A-Za-z]*", "", line) for line in current_context.content]
        sanitized_block = Block(current_context.type, list(current_context.params), sanitized_lines)
        result.append(
            TuneBlock(
                set_directives=dict(set_directives),
                template_block=current_context,
                match_block=sanitized_block,
                add_blocks=list(pending_add_blocks),
            )
        )
        current_context = None
        pending_add_blocks = []

    for block in blocks:
        block_name = block.type
        if block_name.startswith("set"):
            if len(block.params) < 2:
                raise ValueError(f"set block requires at least two parameters: {block}")
            key, value = block.params[:2]
            set_directives[key] = value
            continue
        if block_name.startswith("context"):
            flush_current()
            current_context = block
            pending_add_blocks = []
            continue
        if block_name.startswith("add"):
            if current_context is None:
                raise ValueError("add block encountered before any context block")
            pending_add_blocks.append(block)
            continue
    flush_current()

    logger.debug("parsed %d tune blocks", len(result))
    return result


def _replace_context(filename: str, context: list[str], target: list[str], target_dir: str | Path) -> bool:
    """Replace ``context`` with ``target`` inside the resolved file."""

    normalized_filename = filename.replace("\\", "/")
    path = Path(target_dir) / normalized_filename
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")

    content = path.read_text(encoding="utf-8").splitlines(keepends=True)
    context_norm = [_normalize_line(line) for line in context]

    if not context_norm:
        raise ValueError("empty context block")

    for index in range(0, len(content) - len(context) + 1):
        window = content[index : index + len(context)]
        if all(_normalize_line(a) == b for a, b in zip(window, context_norm, strict=False)):
            updated = content[:index] + target + content[index + len(context) :]
            path.write_text("".join(updated), encoding="utf-8")
            return True
    return False


def _add_content(filename: str, marker: str, lines: list[str], target_dir: str | Path) -> bool:
    """Insert ``lines`` immediately after the first occurrence of ``marker``."""

    normalized_filename = filename.replace("\\", "/")
    path = Path(target_dir) / normalized_filename
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")

    content = path.read_text(encoding="utf-8").splitlines(keepends=True)
    new_content: list[str] = []
    is_marker_found = False

    for line in content:
        new_content.append(line)
        if not is_marker_found and marker in line:
            new_content.extend(lines)
            is_marker_found = True

    if not is_marker_found:
        raise ValueError(f"marker '{marker}' not found in {normalized_filename}")

    path.write_text("".join(new_content), encoding="utf-8")
    return True


def tune_parameters(tune_file: str | Path, params_file: str | Path, target_dir: str | Path) -> None:
    """Synchronize parameter files and patch target sources for SPSA tuning."""

    logger.debug("start tune_parameters()")
    params = read_params(params_file)
    params_by_name: dict[str, ParamEntry] = {entry.name: entry for entry in params}
    for entry_item in params:
        entry_item.is_not_used = True

    tune_blocks = read_tune_file(tune_file)
    prepared_blocks: list[tuple[TuneBlock, str, ParsedContent]] = []

    for tune_block in tune_blocks:
        template_block = tune_block.template_block
        prefix = template_block.params[0] if template_block.params else ""
        parsed = _parse_content_block(template_block, prefix)
        prepared_blocks.append((tune_block, prefix, parsed))
        for param_name, number in zip(parsed.parameter_names, parsed.removed_numbers, strict=False):
            num_val = float(number)
            existing = params_by_name.get(param_name)
            if existing is None:
                if num_val > 0:
                    min_val, max_val = 0.0, num_val * 2.0
                elif num_val == 0.0:
                    min_val, max_val = 0.0, 1.0
                else:
                    min_val, max_val = num_val * 2.0, 0.0
                step = max((max_val - min_val) / 20.0, 1.0)
                delta = 0.0020
                param_type = "int" if num_val == int(num_val) else "float"
                new_entry = ParamEntry(
                    name=param_name,
                    type=param_type,
                    value=num_val,
                    min=min_val,
                    max=max_val,
                    step=step,
                    delta=delta,
                    comment="",
                    is_not_used=False,
                )
                params.append(new_entry)
                params_by_name[param_name] = new_entry
            else:
                existing.is_not_used = False
        for param_name in parsed.parameter_names:
            existing_entry = params_by_name.get(param_name)
            if existing_entry is not None:
                existing_entry.is_not_used = False

    write_params(params_file, params)
    total_params = len(params)
    used_params = sum(1 for entry in params if not entry.is_not_used)

    total_ctx = 0
    applied_ctx = 0
    total_add = 0
    applied_add = 0

    for tune_block, prefix, parsed in prepared_blocks:
        filename = tune_block.set_directives.get("file")
        if filename is None:
            raise ValueError("set block must define a 'file' directive")

        context_lines = tune_block.match_block.content
        is_replaced = False

        for add_block in tune_block.add_blocks:
            block_name = add_block.params[0] if add_block.params else ""
            parsed_add = _parse_content_block(add_block, prefix)
            if block_name:
                logger.debug("add block, prefix=%s, name=%s", prefix, block_name)
                total_add += 1
                if _add_content(filename, block_name, parsed_add.lines, target_dir):
                    applied_add += 1
            else:
                logger.debug("replace block, prefix=%s", prefix)
                total_ctx += 1
                if _replace_context(filename, context_lines, parsed_add.lines, target_dir):
                    applied_ctx += 1
                is_replaced = True

        if not is_replaced:
            logger.debug("modified block, prefix=%s", prefix)
            total_ctx += 1
            if _replace_context(filename, context_lines, parsed.lines, target_dir):
                applied_ctx += 1

        declarations: list[str] = []
        options: list[str] = []

        for param_name in parsed.parameter_names:
            param_entry = params_by_name.get(param_name)
            if param_entry is None:
                raise RuntimeError(f"parameter missing after collection: {param_name}")
            value = int(param_entry.value) if param_entry.type == "int" else param_entry.value
            comment_suffix = f"//{param_entry.comment}" if param_entry.comment else ""
            declarations.append(f"{param_entry.type} {param_entry.name} = {value}; {comment_suffix}\n")
            options.append(
                f"TUNE(SetRange({param_entry.min}, {param_entry.max}), {param_entry.name}, SetDefaultRange);\n"
            )

        declaration_marker = tune_block.set_directives.get("declaration")
        if declaration_marker:
            _add_content(filename, declaration_marker, declarations, target_dir)

        options_marker = tune_block.set_directives.get("options")
        if options_marker:
            _add_content(filename, options_marker, options, target_dir)

    logger.info(
        "[SPSA:tune] applied contexts: %d/%d, add blocks: %d/%d, used params: %d/%d",
        applied_ctx,
        total_ctx,
        applied_add,
        total_add,
        used_params,
        total_params,
    )


__all__ = ["read_tune_file", "tune_parameters"]
