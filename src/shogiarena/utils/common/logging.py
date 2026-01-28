import logging
from collections.abc import Sequence


def setup_logging(log_level: str, *, debug_loggers: Sequence[str] | None = None) -> None:
    """Configure root logging uniformly for CLI tools.

    Mirrors the simple console-oriented configuration used by runners.

    Args:
        log_level: One of DEBUG/INFO/WARNING/ERROR
    """
    numeric_level = getattr(logging, log_level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {log_level}")

    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Tame noisy libraries
    if log_level.upper() == "DEBUG":
        logging.getLogger("asyncio").setLevel(logging.INFO)
        logging.getLogger("concurrent").setLevel(logging.INFO)
    else:
        logging.getLogger("asyncio").setLevel(logging.WARNING)
        logging.getLogger("concurrent").setLevel(logging.WARNING)

    # Quiet third-party access logs if present
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    if debug_loggers:
        for name in debug_loggers:
            if not name:
                continue
            logging.getLogger(name).setLevel(logging.DEBUG)
