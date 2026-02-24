"""Tournament-related API handlers for the arena dashboard."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote

import rshogi
from aiohttp import web
from rshogi.core import Board, Move
from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from shogiarena.arena.services.persistence.db_service import ArenaDBService
from shogiarena.arena.services.statistics.btd import BTDEstimator
from shogiarena.db import Game, Player, ShogiRepository
from shogiarena.db.factory import SQLiteShogiDBFactory
from shogiarena.records.storage.db_store import DBRecordStore
from shogiarena.utils.types.coerce import coerce_game_result, strict_int
from shogiarena.utils.types.types import EngineOptionsSnapshots, GameRecordPlayersDict
from shogiarena.web.dashboard.backend.http_helpers import json_error_response
from shogiarena.web.dashboard.backend.tournament.types import (
    PairStatsEntry,
    ProgressPayload,
    StandingEntry,
    StandingsPayload,
    TournamentGameEntry,
)

logger = logging.getLogger(__name__)


class TournamentAPI:
    """Expose tournament endpoints used by the dashboard."""

    def __init__(
        self,
        *,
        db_path: Path,
        run_dir: Path,
        ensure_shogidb: Callable[[], None],
        shogidb_supplier: Callable[[], ShogiRepository | None],
        engine_options_supplier: Callable[[], EngineOptionsSnapshots] | None = None,
        engine_info_supplier: Callable[[], dict[str, dict[str, str]]] | None = None,
    ) -> None:
        self._db_path = db_path
        self._run_dir = run_dir
        self._ensure_shogidb = ensure_shogidb
        self._get_shogidb = shogidb_supplier
        self._engine_options_supplier = engine_options_supplier
        self._engine_info_supplier = engine_info_supplier
        self._tournament_sse_seq = 0

    @staticmethod
    def _hash_games_payload(games: Sequence[Mapping[str, object]]) -> str:
        try:
            payload = json.dumps(list(games), sort_keys=True, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return hashlib.sha256(json.dumps({"fallback": time.time()}).encode("utf-8")).hexdigest()
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _current_timestamp_iso() -> str:
        return datetime.now(tz=timezone.utc).isoformat()

    @staticmethod
    def _initial_turn(initial_sfen: str | None) -> str:
        if not initial_sfen or initial_sfen == "startpos":
            return "b"
        parts = initial_sfen.split()
        if len(parts) > 1 and parts[1] in {"b", "w"}:
            return parts[1]
        return "b"

    @classmethod
    def _is_mover_black(cls, initial_turn: str, ply_index: int) -> bool:
        move_number = ply_index + 1
        if initial_turn == "b":
            return move_number % 2 == 1
        return move_number % 2 == 0

    @classmethod
    def _compute_eval_arrays(
        cls,
        values: object,
        initial_sfen: str | None,
        expected_length: int,
    ) -> tuple[list[float | None], list[float | None]]:
        if not isinstance(values, list):
            return ([None] * expected_length, [None] * expected_length)

        initial_turn = cls._initial_turn(initial_sfen)
        eval_black: list[float | None] = []
        eval_white: list[float | None] = []
        for index, raw in enumerate(values):
            if raw is None:
                eval_black.append(None)
                eval_white.append(None)
                continue
            if not isinstance(raw, int | float):
                try:
                    numeric = float(cast(str, raw))
                except (TypeError, ValueError) as error:
                    raise web.HTTPInternalServerError(
                        reason="Tournament eval values must be numeric",
                    ) from error
            else:
                numeric = float(raw)

            mover_is_black = cls._is_mover_black(initial_turn, index)
            if mover_is_black:
                eval_black.append(numeric)
                eval_white.append(-numeric)
            else:
                eval_black.append(-numeric)
                eval_white.append(numeric)

        if len(eval_black) < expected_length:
            padding = expected_length - len(eval_black)
            eval_black.extend([None] * padding)
            eval_white.extend([None] * padding)

        return (eval_black, eval_white)

    @staticmethod
    def _extract_move_entry(
        entry: rshogi.record.MoveRecord,
    ) -> tuple[Move, int | None, rshogi.record.MoveEngineInfo | None]:
        return (entry.move, entry.time_ms, entry.engine_info)

    @staticmethod
    def _compute_pair_los(wins_a: int, wins_b: int, draws: int) -> float | None:
        total = wins_a + wins_b + draws
        if total <= 0:
            return None
        variance = total / 4
        if variance <= 0:
            return None
        score = wins_a + 0.5 * draws
        z = (score - total / 2) / math.sqrt(variance)
        return 0.5 * (1 + math.erf(z / math.sqrt(2)))

    def _next_tournament_seq(self) -> int:
        self._tournament_sse_seq += 1
        return self._tournament_sse_seq

    @staticmethod
    def _extract_last_event_id(request: web.Request) -> int | None:
        raw = request.headers.get("Last-Event-ID")
        if not raw:
            return None
        try:
            parsed = int(raw)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _inject_resume_from(payload: dict[str, object], last_event_id: int | None) -> dict[str, object]:
        if last_event_id is None:
            return payload
        if "resume_from" in payload:
            return payload
        next_payload = dict(payload)
        next_payload["resume_from"] = last_event_id
        return next_payload

    @staticmethod
    def _format_sse_event(event_type: str, payload: dict[str, object], *, event_id: str | None = None) -> bytes:
        parts = []
        if event_id is not None:
            parts.append(f"id: {event_id}")
        parts.append(f"event: {event_type}")
        parts.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
        return ("\n".join(parts) + "\n\n").encode()

    def _build_standings_payload(self) -> StandingsPayload:
        db_service = ArenaDBService(SQLiteShogiDBFactory(self._db_path))
        games = db_service.get_all_games()

        engine_stats: dict[str, dict[str, float]] = {}
        for game in games:
            black = game["black_engine"]
            white = game["white_engine"]
            result = game["result"]

            if black not in engine_stats:
                engine_stats[black] = {"wins": 0, "draws": 0, "losses": 0, "games": 0}
            if white not in engine_stats:
                engine_stats[white] = {"wins": 0, "draws": 0, "losses": 0, "games": 0}

            engine_stats[black]["games"] += 1
            engine_stats[white]["games"] += 1

            game_result = coerce_game_result(result, strict=True)
            if game_result.is_black_win():
                engine_stats[black]["wins"] += 1
                engine_stats[white]["losses"] += 1
            elif game_result.is_white_win():
                engine_stats[white]["wins"] += 1
                engine_stats[black]["losses"] += 1
            elif game_result.is_draw():
                engine_stats[black]["draws"] += 1
                engine_stats[white]["draws"] += 1

        engine_names = {str(game["black_engine"]) for game in games if game.get("black_engine")}
        engine_names |= {str(game["white_engine"]) for game in games if game.get("white_engine")}

        estimator = BTDEstimator().estimate(
            (
                cast(
                    GameRecordPlayersDict,
                    {
                        "black_player": game["black_engine"],
                        "white_player": game["white_engine"],
                        "result": game["result"],
                    },
                )
                for game in games
            ),
            engine_names=engine_names,
        )
        ratings = {name: 1500.0 + float(rating) for name, rating in estimator.ratings.items()}

        standings: list[StandingEntry] = []
        for engine, stats in engine_stats.items():
            points = stats["wins"] + stats["draws"] * 0.5
            win_rate = stats["wins"] / stats["games"] if stats["games"] > 0 else 0
            standings.append(
                {
                    "engine": engine,
                    "points": points,
                    "games": stats["games"],
                    "wins": stats["wins"],
                    "draws": stats["draws"],
                    "losses": stats["losses"],
                    "win_rate": win_rate,
                    "rating": ratings.get(engine, 1500.0),
                    "rank": 0,
                }
            )

        standings.sort(key=lambda entry: (-entry["points"], -entry["rating"]))
        for index, standing in enumerate(standings):
            standing["rank"] = index + 1

        engines_meta: list[dict[str, object]] = []
        summary_btd_path = self._run_dir / "summary_btd.json"
        if summary_btd_path.exists():
            with open(summary_btd_path, encoding="utf-8") as handle:
                summary_data = json.load(handle)
                engines_meta = summary_data.get("enginesMeta") or summary_data.get("engines_meta", [])

        return {
            "standings": standings,
            "enginesMeta": engines_meta,
            "updated_at": datetime.now().isoformat(),
        }

    def _build_progress_payload(self) -> ProgressPayload:
        run_state_path = self._run_dir / "run_state.json"
        if run_state_path.exists():
            state = json.loads(run_state_path.read_text(encoding="utf-8"))
            total = state.get("total_games", 0)
            completed = len(state.get("completed_game_ids", []))
            pending = total - completed
            estimated_minutes = pending * 1
            return {
                "games": {
                    "completed": completed,
                    "total": total,
                    "cancelled": 0,
                },
                "inProgress": 0,
                "pending": pending,
                "completionRate": completed / total if total > 0 else 0,
                "estimatedTimeRemaining": f"{estimated_minutes:02d}:00:00",
                "updatedAt": datetime.now().isoformat(),
            }

        db_service = ArenaDBService(SQLiteShogiDBFactory(self._db_path))
        games = db_service.get_all_games()
        completed = len(games)
        return {
            "games": {
                "completed": completed,
                "total": -1,
                "cancelled": 0,
            },
            "inProgress": 0,
            "pending": -1,
            "completionRate": -1,
            "estimatedTimeRemaining": "Unknown",
            "updatedAt": datetime.now().isoformat(),
        }

    def register_routes(self, app: web.Application) -> None:
        """Register tournament endpoints on the aiohttp app."""
        app.router.add_get("/api/games", self.get_games)
        app.router.add_get("/api/game/{game_id}", self.get_game)
        app.router.add_get("/api/standings", self.get_standings)
        app.router.add_get("/api/head_to_head", self.get_head_to_head)
        app.router.add_get("/api/progress", self.get_progress)
        app.router.add_get("/api/tournament/summary/stream", self.sse_tournament_summary)
        app.router.add_get("/api/engine_options/{engine_name}", self.get_engine_options)
        app.router.add_get("/api/tournament/match-history", self.get_match_history)
        app.router.add_get("/api/tournament/pair-stats", self.get_pair_stats)

    async def get_games(self, request: web.Request) -> web.Response:
        offset = int(request.rel_url.query.get("offset", 0))
        limit = min(int(request.rel_url.query.get("limit", 100)), 1000)
        search_query = request.rel_url.query.get("q", "")

        self._ensure_shogidb()
        shogidb = self._get_shogidb()
        if shogidb is not None:
            session = shogidb.session
            Black = aliased(Player)
            White = aliased(Player)

            count_stmt = (
                select(func.count()).select_from(Game).join(Black, Game.black_player).join(White, Game.white_player)
            )
            if search_query:
                like = f"%{search_query}%"
                count_stmt = count_stmt.where(
                    or_(
                        Black.player_name.like(like),
                        White.player_name.like(like),
                        Game.game_name.like(like),
                    )
                )
            total = session.execute(count_stmt).scalar_one()

            data_stmt = (
                select(
                    Game.game_name,
                    Black.player_name.label("black_player"),
                    White.player_name.label("white_player"),
                    Game.result_code,
                    Game.num_moves,
                    Game.end_date,
                    Game.init_position_sfen,
                    Game.time_control_black,
                    Game.time_control_white,
                )
                .join(Black, Game.black_player)
                .join(White, Game.white_player)
            )
            if search_query:
                like = f"%{search_query}%"
                data_stmt = data_stmt.where(
                    or_(
                        Black.player_name.like(like),
                        White.player_name.like(like),
                        Game.game_name.like(like),
                    )
                )
            data_stmt = data_stmt.order_by(Game.end_date.desc()).limit(limit).offset(offset)

            rows = session.execute(data_stmt).all()
            games: list[TournamentGameEntry] = []
            for (
                game_name,
                black_player,
                white_player,
                result_code,
                num_moves,
                end_date,
                init_sfen,
                time_control_black,
                time_control_white,
            ) in rows:
                games.append(
                    {
                        "game_id": game_name,
                        "black_player": black_player,
                        "white_player": white_player,
                        "result_code": int(result_code),
                        "total_plies": int(num_moves),
                        "end_time": end_date.isoformat() if end_date else None,
                        "initial_sfen": init_sfen,
                        "time_control_black": time_control_black,
                        "time_control_white": time_control_white,
                    }
                )

            return web.json_response(
                {
                    "games": games,
                    "total": int(total),
                    "offset": offset,
                    "limit": limit,
                }
            )

        return web.json_response({"games": [], "total": 0, "offset": offset, "limit": limit})

    async def get_match_history(self, request: web.Request) -> web.Response:
        query = request.rel_url.query
        try:
            limit_raw = int(query.get("limit", 50))
        except (TypeError, ValueError):
            limit_raw = 50
        try:
            offset_raw = int(query.get("offset", 0))
        except (TypeError, ValueError):
            offset_raw = 0

        limit = max(1, min(limit_raw, 500))
        offset = max(0, offset_raw)

        def _format_game_row(row: Mapping[str, object]) -> TournamentGameEntry:
            end_time = row.get("end_time")
            if isinstance(end_time, datetime):
                end_time_iso = end_time.isoformat()
            elif isinstance(end_time, str):
                end_time_iso = end_time
            else:
                end_time_iso = None
            result_code = row.get("result_code")
            return {
                "game_id": row.get("game_id"),
                "black_player": row.get("black_player"),
                "white_player": row.get("white_player"),
                "result_code": int(result_code) if isinstance(result_code, (int | float)) else result_code,
                "total_plies": row.get("total_plies"),
                "end_time": end_time_iso,
                "initial_sfen": row.get("initial_sfen"),
                "time_control_black": row.get("time_control_black"),
                "time_control_white": row.get("time_control_white"),
            }

        games_payload: list[TournamentGameEntry] = []
        total_count = 0

        self._ensure_shogidb()
        shogidb = self._get_shogidb()
        if shogidb is not None:
            session = shogidb.session
            Black = aliased(Player)
            White = aliased(Player)

            count_stmt = select(func.count()).where(Game.game_type == "arena")
            total_count = int(session.execute(count_stmt).scalar_one())

            data_stmt = (
                select(
                    Game.game_name.label("game_id"),
                    Black.player_name.label("black_player"),
                    White.player_name.label("white_player"),
                    Game.result_code,
                    Game.num_moves.label("total_plies"),
                    Game.end_date.label("end_time"),
                    Game.init_position_sfen.label("initial_sfen"),
                    Game.time_control_black,
                    Game.time_control_white,
                )
                .join(Black, Game.black_player)
                .join(White, Game.white_player)
                .where(Game.game_type == "arena")
                .order_by(Game.end_date.desc(), Game.id.desc())
                .limit(limit)
                .offset(offset)
            )

            rows = session.execute(data_stmt).all()
            for row in rows:
                games_payload.append(
                    _format_game_row(
                        {
                            "game_id": row.game_id,
                            "black_player": row.black_player,
                            "white_player": row.white_player,
                            "result_code": row.result_code,
                            "total_plies": row.total_plies,
                            "end_time": row.end_time,
                            "initial_sfen": row.initial_sfen,
                            "time_control_black": row.time_control_black,
                            "time_control_white": row.time_control_white,
                        }
                    )
                )
        signature = self._hash_games_payload(games_payload)
        source = "db" if shogidb is not None else "unavailable"

        return web.json_response(
            {
                "games": games_payload,
                "limit": limit,
                "offset": offset,
                "total": total_count,
                "signature": signature,
                "source": source,
                "fetched_at": self._current_timestamp_iso(),
            }
        )

    async def get_game(self, request: web.Request) -> web.Response:
        game_id = request.match_info["game_id"]

        self._ensure_shogidb()
        shogidb = self._get_shogidb()
        if shogidb is not None:
            record_store = DBRecordStore(shogidb)
            record = record_store.load(game_name=game_id)
            if record is None:
                return json_error_response("Game not found", status=404, code="game_not_found")
            metadata = record.metadata
            game_id_value = record.game_name or game_id
            black_player = metadata.black_player or ""
            white_player = metadata.white_player or ""
            black_tc = record.black_time_control
            white_tc = record.white_time_control
            tc_black = black_tc.to_spec() if black_tc is not None else None
            tc_white = white_tc.to_spec() if white_tc is not None else None
            start_time = metadata.start_date
            end_time = metadata.end_date

            initial_sfen_raw = record.init_position_sfen
            initial_sfen = initial_sfen_raw if initial_sfen_raw != "startpos" else None
            board = Board()
            if initial_sfen:
                board.set_sfen(initial_sfen)
            moves_usi: list[str] = []
            moves_ki2: list[str] = []
            eval_values: list[int | None] = []
            nodes_values: list[int | None] = []
            depth_values: list[int | None] = []
            seldepth_values: list[int | None] = []
            move_times_ms: list[int | None] = []
            wall_times_ms: list[int | None] = []
            latency_deltas_ms: list[int | None] = []
            for move_entry in record.moves:
                mv, move_time, engine_info = self._extract_move_entry(move_entry)
                move_text = mv.to_usi()
                if not board.is_legal_move(mv):
                    logger.error("Illegal move: %s", move_text)
                    break
                moves_ki2.append(board.move32_from_move(mv).to_ki2(board) or mv.to_usi())
                moves_usi.append(move_text)
                board.apply_move(mv)

                eval_cp = engine_info.eval if engine_info is not None else None
                nodes = engine_info.nodes if engine_info is not None else None
                depth = engine_info.depth if engine_info is not None else None
                seldepth = engine_info.seldepth if engine_info is not None else None
                wall_time = engine_info.wall_time_ms if engine_info is not None else None
                latency_delta = engine_info.latency_delta_ms if engine_info is not None else None
                move_time_value = strict_int(move_time)
                eval_value = strict_int(eval_cp)
                nodes_value = strict_int(nodes)
                depth_value = strict_int(depth)
                seldepth_values.append(strict_int(seldepth))
                wall_time_value = strict_int(wall_time)
                latency_deltas_ms.append(strict_int(latency_delta))
                move_times_ms.append(move_time_value)
                eval_values.append(eval_value)
                nodes_values.append(nodes_value)
                depth_values.append(depth_value)
                wall_times_ms.append(wall_time_value)

            if not any(value is not None for value in move_times_ms):
                move_times_ms = []
            if not any(value is not None for value in wall_times_ms):
                wall_times_ms = []
            if not any(value is not None for value in latency_deltas_ms):
                latency_deltas_ms = []

            eval_black, eval_white = self._compute_eval_arrays(
                eval_values,
                initial_sfen_raw,
                len(moves_usi),
            )

            result_obj = record.result
            game_data = {
                "game_id": game_id_value,
                "black_player": black_player,
                "white_player": white_player,
                "result_code": result_obj.value if result_obj is not None else None,
                "initial_sfen": initial_sfen_raw,
                "time_control_black": tc_black,
                "time_control_white": tc_white,
                "moves": moves_usi,
                "ki2_moves": moves_ki2,
                "eval_black": eval_black,
                "eval_white": eval_white,
                "nodes_values": nodes_values,
                "depth_values": depth_values,
                "seldepth_values": seldepth_values,
                "move_times_ms": move_times_ms,
                "wall_times_ms": wall_times_ms,
                "latency_deltas_ms": latency_deltas_ms,
                "total_plies": len(record.moves),
                "start_time": start_time,
                "end_time": end_time,
            }

            return web.json_response(game_data)

        return json_error_response("Game not found", status=404, code="game_not_found")

    async def get_standings(self, request: web.Request) -> web.Response:
        return web.json_response(self._build_standings_payload())

    async def get_head_to_head(self, request: web.Request) -> web.Response:
        db_service = ArenaDBService(SQLiteShogiDBFactory(self._db_path))
        games = db_service.get_all_games()

        head_to_head: dict[tuple[str, str], dict[str, Any]] = {}
        for game in games:
            black = str(game["black_engine"])
            white = str(game["white_engine"])
            result = game["result"]

            pair = (black, white) if black <= white else (white, black)
            if pair not in head_to_head:
                head_to_head[pair] = {
                    "engines": list(pair),
                    f"{pair[0]}_wins": 0,
                    f"{pair[1]}_wins": 0,
                    "draws": 0,
                    "total": 0,
                }

            head_to_head[pair]["total"] = int(head_to_head[pair].get("total", 0)) + 1

            game_result = coerce_game_result(result, strict=True)
            if game_result.is_black_win():
                if black == pair[0]:
                    head_to_head[pair][f"{pair[0]}_wins"] = int(head_to_head[pair].get(f"{pair[0]}_wins", 0)) + 1
                else:
                    head_to_head[pair][f"{pair[1]}_wins"] = int(head_to_head[pair].get(f"{pair[1]}_wins", 0)) + 1
            elif game_result.is_white_win():
                if white == pair[0]:
                    head_to_head[pair][f"{pair[0]}_wins"] = int(head_to_head[pair].get(f"{pair[0]}_wins", 0)) + 1
                else:
                    head_to_head[pair][f"{pair[1]}_wins"] = int(head_to_head[pair].get(f"{pair[1]}_wins", 0)) + 1
            elif game_result.is_draw():
                head_to_head[pair]["draws"] = int(head_to_head[pair].get("draws", 0)) + 1

        return web.json_response(
            {"head_to_head": list(head_to_head.values()), "updated_at": datetime.now().isoformat()}
        )

    async def get_pair_stats(self, request: web.Request) -> web.Response:
        db_service = ArenaDBService(SQLiteShogiDBFactory(self._db_path))
        games = db_service.get_all_games()

        pair_records: dict[tuple[str, str], dict[str, Any]] = {}
        for game in games:
            black = str(game["black_engine"])
            white = str(game["white_engine"])
            result = game["result"]
            if not black or not white:
                continue
            pair = (black, white) if black <= white else (white, black)
            record = pair_records.setdefault(
                pair,
                {
                    "engines": pair,
                    "wins": {pair[0]: 0, pair[1]: 0},
                    "draws": 0,
                    "games": 0,
                },
            )

            record["games"] += 1
            game_result = coerce_game_result(result, strict=True)
            if game_result.is_black_win():
                winner = black
            elif game_result.is_white_win():
                winner = white
            elif game_result.is_draw():
                record["draws"] = int(record.get("draws", 0)) + 1
                continue
            else:
                continue

            wins_map = record["wins"]
            wins_map[winner] = int(wins_map.get(winner, 0)) + 1

        pairs_payload: list[PairStatsEntry] = []
        for (engine_a, engine_b), record in pair_records.items():
            total = int(record.get("games", 0))
            draws = int(record.get("draws", 0))
            wins_map = record.get("wins", {})
            wins_a = int(wins_map.get(engine_a, 0))
            wins_b = int(wins_map.get(engine_b, 0))
            win_rate_a = (wins_a + 0.5 * draws) / total if total > 0 else None
            win_rate_b = (wins_b + 0.5 * draws) / total if total > 0 else None
            los_a = self._compute_pair_los(wins_a, wins_b, draws)
            los_b = 1 - los_a if los_a is not None else None
            pairs_payload.append(
                {
                    "pair_id": f"{engine_a}__vs__{engine_b}",
                    "engines": [engine_a, engine_b],
                    "games": total,
                    "wins": {engine_a: wins_a, engine_b: wins_b},
                    "draws": draws,
                    "win_rate": {engine_a: win_rate_a, engine_b: win_rate_b},
                    "los": {engine_a: los_a, engine_b: los_b},
                }
            )

        pairs_payload.sort(key=lambda entry: (-int(entry["games"]), entry["pair_id"]))

        signature = self._hash_games_payload(pairs_payload)
        source = "db"

        return web.json_response(
            {
                "pairs": pairs_payload,
                "total_pairs": len(pairs_payload),
                "signature": signature,
                "source": source,
                "fetched_at": self._current_timestamp_iso(),
            }
        )

    async def get_progress(self, request: web.Request) -> web.Response:
        return web.json_response(self._build_progress_payload())

    async def sse_tournament_summary(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await response.prepare(request)

        last_event_id = self._extract_last_event_id(request)
        poll_interval = float(request.rel_url.query.get("poll_interval", 5.0) or 5.0)
        poll_interval = max(1.0, min(poll_interval, 30.0))
        send_initial = request.rel_url.query.get("send_initial", "true").lower() != "false"
        heartbeat_interval = 15.0
        last_heartbeat = time.monotonic()
        last_signature: str | None = None

        async def push_event(payload: dict[str, object]) -> None:
            payload = self._inject_resume_from(payload, last_event_id)
            seq = self._next_tournament_seq()
            envelope = dict(payload)
            envelope.setdefault("stream", "tournament_summary")
            envelope["seq"] = seq
            chunk = self._format_sse_event("tournament_summary", envelope, event_id=str(seq))
            await response.write(chunk)
            await response.drain()

        try:
            while True:
                summary_payload = {
                    **self._build_standings_payload(),
                    **self._build_progress_payload(),
                }
                signature = json.dumps(summary_payload, sort_keys=True, ensure_ascii=False)
                if send_initial or signature != last_signature:
                    await push_event(
                        {
                            "type": "summary_update",
                            "data": summary_payload,
                            "timestamp": int(time.time() * 1000),
                        }
                    )
                    last_signature = signature
                    send_initial = False

                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    await push_event({"type": "heartbeat", "timestamp": int(time.time() * 1000)})
                    last_heartbeat = now

                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            raise
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            logger.debug("Tournament summary SSE client disconnected")
        except Exception:
            logger.exception("Unexpected error in tournament summary SSE")

        return response

    async def get_engine_options(self, request: web.Request) -> web.Response:
        engine_name = request.match_info["engine_name"]
        supplier = self._engine_options_supplier
        options_map = supplier() if callable(supplier) else {}
        options = options_map.get(engine_name)
        if options is None:
            decoded = unquote(engine_name)
            if decoded != engine_name:
                engine_name = decoded
                options = options_map.get(engine_name)
        if options is None:
            lowered = engine_name.lower()
            for key, value in options_map.items():
                if key.lower() == lowered:
                    engine_name = key
                    options = value
                    break
        if options is None:
            return json_error_response(
                f"Engine '{engine_name}' options not found",
                status=404,
                code="engine_not_found",
                engine=engine_name,
                options={},
            )

        info_supplier = self._engine_info_supplier
        info_map = info_supplier() if callable(info_supplier) else {}
        info = info_map.get(engine_name, {})

        return web.json_response(
            {
                "engine": engine_name,
                "options": options,
                "info": info,
                "updated_at": datetime.now().isoformat(),
            }
        )
