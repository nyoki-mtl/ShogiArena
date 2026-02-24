from shogiarena.utils.common.settings import SETTINGS, ArenaSettings

output_dir = SETTINGS.output_dir
engine_dir = SETTINGS.engine_dir
repos = SETTINGS.repos
overlays = SETTINGS.overlays
settings_path = SETTINGS.settings_path
log_root_dir = output_dir / "logs"


def _apply_settings(settings: ArenaSettings) -> None:
    global output_dir
    global engine_dir
    global repos
    global overlays
    global log_root_dir
    global settings_path

    output_dir = settings.output_dir
    engine_dir = settings.engine_dir
    repos = settings.repos
    overlays = settings.overlays
    log_root_dir = output_dir / "logs"
    settings_path = settings.settings_path
