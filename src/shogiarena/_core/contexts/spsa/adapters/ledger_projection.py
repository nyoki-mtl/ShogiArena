"""Derived compatibility artifacts built from the SPSA ledger."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from shogiarena._core.shared.kernel.atomic_json import write_json_atomic
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_int

_PROJECTION_SCHEMA = "shogiarena.spsa.ledger-projection.v1"


def project_spsa_ledger(
    *,
    connection: sqlite3.Connection,
    run_id: str,
    run_dir: Path,
) -> None:
    """Atomically rebuild current/index/events compatibility views from ledger."""

    contract_row = connection.execute(
        "SELECT contract_json FROM run_contract WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if contract_row is None:
        raise ValueError(f"SPSA ledger run contract is missing for {run_id}")
    contract = _object(str(contract_row[0]), label="run contract")
    config_raw = contract.get("config")
    config = config_raw if isinstance(config_raw, dict) else {}

    rows = connection.execute(
        """
        SELECT update_idx, state, theta_before_json, theta_candidate_json,
               theta_final_json, schedule_json, ltc_required, updated_at
        FROM updates WHERE run_id = ? ORDER BY update_idx
        """,
        (run_id,),
    ).fetchall()
    completed = _contiguous_committed(rows)
    theta = _current_theta(connection, run_id=run_id, rows=rows, completed=completed)
    pair_index_end = 0
    updates: list[JsonObject] = []
    for row in rows:
        update_idx = int(row[0])
        if update_idx > completed or str(row[1]) != "COMMITTED":
            continue
        schedule = _object(str(row[5] or "{}"), label=f"update {update_idx} schedule")
        pair_index_end = coerce_int(schedule.get("pair_index_end")) or pair_index_end
        entry: JsonObject = {
            "schema_version": "shogiarena.spsa.update.v1",
            "projection_source": "spsa/ledger.sqlite3",
            "update_idx": update_idx,
            "timestamp": _epoch_ms(str(row[7])),
            "params": _object(str(row[4]), label=f"update {update_idx} final theta"),
            "theta_before": _object(str(row[2]), label=f"update {update_idx} prior theta"),
            "theta_candidate": _object(str(row[3]), label=f"update {update_idx} candidate theta"),
            "ltc_required": bool(row[6]),
            **schedule,
        }
        updates.append(entry)

    spsa_dir = run_dir / "spsa"
    current: JsonObject = {
        "schema_version": "shogiarena.spsa.current.v1",
        "projection_schema": _PROJECTION_SCHEMA,
        "projection_source": "spsa/ledger.sqlite3",
        "update_idx": completed,
        "pair_index_end": pair_index_end,
        "theta": theta,
    }
    metadata: JsonObject = {
        "projection_schema": _PROJECTION_SCHEMA,
        "projection_source": "spsa/ledger.sqlite3",
        "last_update_idx": completed,
        "total_updates": len(updates),
        "last_updated": updates[-1]["timestamp"] if updates else 0,
        "int_rounding_policy": config.get("int_rounding"),
        "crn_used": config.get("crn_enabled"),
    }
    write_json_atomic(spsa_dir / "current.json", current)
    write_json_atomic(spsa_dir / "index.json", {"metadata": metadata, "updates": updates})
    _write_events_atomic(
        spsa_dir / "events.jsonl",
        _project_events(connection, run_id=run_id, update_rows=rows),
    )


def _project_events(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    update_rows: list[tuple[object, ...]],
) -> list[JsonObject]:
    events: list[JsonObject] = []
    for row in connection.execute(
        """
        SELECT observations.game_id, observations.result_kind,
               observations.observation_kind, observations.observed_at,
               pairs.update_idx, pairs.color_assignment_json
        FROM game_observations AS observations
        JOIN pair_assignments AS pairs
          ON pairs.run_id = observations.run_id
         AND pairs.pair_id = observations.pair_id
        WHERE observations.run_id = ?
        ORDER BY observations.observed_at, observations.game_id
        """,
        (run_id,),
    ):
        game_id = str(row[0])
        result_kind = str(row[1])
        assignment = _object(str(row[5]), label=f"game {game_id} color assignment")
        tuned_as_black = _tuned_as_black(assignment, game_id=game_id)
        events.append(
            {
                "event": "game_result",
                "projection_schema": _PROJECTION_SCHEMA,
                "projection_source": "spsa/ledger.sqlite3",
                "update_idx": int(row[4]),
                "winner": _winner_code(result_kind, tuned_as_black=tuned_as_black),
                "tuned_as_black": tuned_as_black,
                "phase": "ltc" if str(row[2]) == "LTC" else "tuning",
                "family": str(row[2]).lower(),
                "is_ltc": str(row[2]) == "LTC",
                "game_id": game_id,
                "game_result": result_kind,
                "ts": _epoch_ms(str(row[3])),
            }
        )
    for row in update_rows:
        if str(row[1]) != "COMMITTED":
            schedule = _object(str(row[5] or "{}"), label=f"update {row[0]} schedule")
            events.append(
                {
                    "event": "update_pending",
                    "projection_schema": _PROJECTION_SCHEMA,
                    "projection_source": "spsa/ledger.sqlite3",
                    "update_idx": int(str(row[0])),
                    "params": _object(str(row[2]), label=f"update {row[0]} prior theta"),
                    "timestamp": _epoch_ms(str(row[7])),
                    "is_pending": True,
                    "ledger_stage": str(row[1]),
                    **schedule,
                }
            )
            continue
        schedule = _object(str(row[5] or "{}"), label=f"update {row[0]} schedule")
        events.append(
            {
                "event": "update",
                "projection_schema": _PROJECTION_SCHEMA,
                "projection_source": "spsa/ledger.sqlite3",
                "update_idx": int(str(row[0])),
                "params": _object(str(row[4]), label=f"update {row[0]} final theta"),
                "timestamp": _epoch_ms(str(row[7])),
                **schedule,
            }
        )
    for revision, event_type, payload_json, created_at in connection.execute(
        """
        SELECT revision, event_type, payload_json, created_at
        FROM event_revisions WHERE run_id = ? ORDER BY revision
        """,
        (run_id,),
    ):
        payload = _object(str(payload_json), label=f"event revision {revision}")
        events.append(
            {
                "event": str(event_type),
                "projection_schema": _PROJECTION_SCHEMA,
                "projection_source": "spsa/ledger.sqlite3",
                "revision": int(revision),
                "ts": _epoch_ms(str(created_at)),
                **payload,
            }
        )
    return sorted(
        events,
        key=lambda event: (
            coerce_int(event.get("ts", event.get("timestamp", 0))) or 0,
            str(event.get("event")),
        ),
    )


def _contiguous_committed(rows: list[tuple[object, ...]]) -> int:
    expected = 1
    for row in rows:
        idx = int(str(row[0]))
        if idx != expected or str(row[1]) != "COMMITTED":
            break
        expected += 1
    return expected - 1


def _current_theta(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    rows: list[tuple[object, ...]],
    completed: int,
) -> JsonObject:
    if completed:
        return _object(str(rows[completed - 1][4]), label="current theta")
    return {
        str(name): float(str(value))
        for name, value in connection.execute(
            "SELECT parameter_id, initial_value FROM parameters WHERE run_id = ? ORDER BY ordinal",
            (run_id,),
        )
    }


def _tuned_as_black(assignment: JsonObject, *, game_id: str) -> bool:
    games = assignment.get("games")
    if not isinstance(games, list):
        raise ValueError(f"SPSA pair assignment has no games for {game_id}")
    for game in games:
        if isinstance(game, dict) and game.get("game_id") == game_id:
            return game.get("tuned_as") == "black"
    raise ValueError(f"SPSA pair assignment does not contain game {game_id}")


def _winner_code(result_kind: str, *, tuned_as_black: bool) -> int | None:
    if result_kind == "DRAW":
        return 2
    if result_kind == "BLACK_WIN":
        return 1 if tuned_as_black else -1
    if result_kind == "WHITE_WIN":
        return -1 if tuned_as_black else 1
    return None


def _object(raw: str, *, label: str) -> JsonObject:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"SPSA {label} is not a JSON object")
    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    raise ValueError(f"Unsupported SPSA projection value: {type(value).__name__}")


def _epoch_ms(raw: str) -> int:
    return int(datetime.fromisoformat(raw).timestamp() * 1000)


def _write_events_atomic(path: Path, events: list[JsonObject]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temp_path.replace(path)


__all__ = ["project_spsa_ledger"]
