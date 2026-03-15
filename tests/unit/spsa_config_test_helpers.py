from pathlib import Path

from shogiarena._core.contexts.game_session.adapters.orchestration.config_spsa_parser import parse_spsa_config_mapping
from shogiarena._core.interfaces.cli.config_file_loaders import parse_spsa_config_file


def load_spsa_run_config(config_path: Path):
    payload = parse_spsa_config_file(config_path)
    return parse_spsa_config_mapping(payload, source_path=config_path)
