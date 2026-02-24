"""Implementation of the ``shogiarena run generate`` command."""

from __future__ import annotations

import argparse

from . import tournament as tournament_cmd
from .tournament_like import add_tournament_common_args, flatten_block_tokens, run_tournament_like


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Generate サブコマンドを登録する。"""

    parser = subparsers.add_parser(
        "generate",
        help="Generate kifu via selfplay from a YAML configuration",
    )
    add_tournament_common_args(
        parser,
        include_tournament=False,
        include_generate=True,
        include_rating=False,
        include_sprt=False,
    )
    parser.set_defaults(handler=_run_generate)


def _run_generate(args: argparse.Namespace) -> None:
    sections = {
        "rules": flatten_block_tokens(args.rules),
        "generate": flatten_block_tokens(args.generate),
        "dashboard": flatten_block_tokens(args.dashboard),
        "system": flatten_block_tokens(args.system),
    }
    run_tournament_like(
        args,
        require_sprt=False,
        default_experiment="generate",
        label="generate",
        sections_override=sections,
        run_sync=tournament_cmd.run_generate_sync,
    )
