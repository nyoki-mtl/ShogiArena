"""Archived tournament schedule projection for the dashboard."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from shogiarena._core.contexts.dashboard.ports.interface_dependencies import DashboardGameQueryPort
from shogiarena._core.shared.kernel.json_coercion import is_str_object_mapping, to_json_object
from shogiarena._core.shared.kernel.json_types import JsonObject

logger = logging.getLogger(__name__)


def _load_object(path: Path) -> JsonObject:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.debug("Failed to read archived dashboard data: %s", path, exc_info=True)
        return {}
    if not is_str_object_mapping(payload):
        return {}
    return to_json_object(payload)


def build_archived_schedule_snapshot(
    run_dir: Path,
    *,
    db_path: Path,
    game_query: DashboardGameQueryPort,
) -> JsonObject | None:
    """Build the read-only schedule view from sealed run artifacts and game DB rows."""

    schedule_payload = _load_object(run_dir / "schedule.json")
    raw_games = schedule_payload.get("games")
    if not isinstance(raw_games, list):
        return None

    state = _load_object(run_dir / "state.json")
    expected_total = state.get("original_total_games")
    query_limit = max(len(raw_games), expected_total if isinstance(expected_total, int) else 0, 1)
    completed_payload = game_query.build_games_raw_payload(
        db_path,
        limit=query_limit,
        offset=0,
        search_query=None,
    )
    completed_rows = completed_payload.get("games")
    completed_by_id: dict[str, JsonObject] = {}
    if isinstance(completed_rows, list):
        for completed_row in completed_rows:
            if not is_str_object_mapping(completed_row):
                continue
            normalized = to_json_object(completed_row)
            game_id = normalized.get("game_id")
            if isinstance(game_id, str) and game_id:
                completed_by_id[game_id] = normalized

    cancelled_raw = state.get("cancelled_game_ids")
    cancelled_ids = (
        {value for value in cancelled_raw if isinstance(value, str) and value}
        if isinstance(cancelled_raw, list)
        else set()
    )

    rows: list[JsonObject] = []
    for index, raw in enumerate(raw_games):
        if not is_str_object_mapping(raw):
            continue
        row = to_json_object(raw)
        game_id = row.get("game_id")
        if not isinstance(game_id, str) or not game_id:
            continue

        display_order = row.get("display_order")
        row["order"] = display_order - 1 if isinstance(display_order, int) and display_order > 0 else index
        if "initial_sfen" not in row and isinstance(row.get("sfen"), str):
            row["initial_sfen"] = row["sfen"]

        completed = completed_by_id.get(game_id)
        if completed is not None:
            row["status"] = "completed"
            for source, target in (
                ("black_player", "black"),
                ("white_player", "white"),
                ("game_result", "game_result"),
                ("total_plies", "total_plies"),
                ("end_time", "end_time"),
                ("initial_sfen", "initial_sfen"),
            ):
                value = completed.get(source)
                if value is not None:
                    row[target] = value
        elif game_id in cancelled_ids:
            row["status"] = "cancelled"
        else:
            row["status"] = "pending"
        rows.append(row)

    completed_count = sum(row.get("status") == "completed" for row in rows)
    cancelled_count = sum(row.get("status") == "cancelled" for row in rows)
    pending_count = max(len(rows) - completed_count - cancelled_count, 0)
    return {
        "revision": 0,
        "total_games": len(rows) - cancelled_count,
        "original_total_games": len(rows),
        "completed_games": completed_count,
        "running_games": 0,
        "pending_games": pending_count,
        "cancelled_games": cancelled_count,
        "is_running": False,
        "session_state": "finished" if pending_count == 0 else "archived",
        "schedule": rows,
    }


__all__ = ["build_archived_schedule_snapshot"]
