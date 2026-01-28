"""HTML dashboard utilities for arena (TypeScript board only)."""

from .backend.assets_writer import init_dashboard_html
from .backend.spsa import SpsaParamsService

__all__ = ["init_dashboard_html", "SpsaParamsService"]
