"""Run-owned incremental SPSA dashboard projector."""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import aliased

from shogiarena._core.contexts.dashboard.adapters.db_repository import open_dashboard_repository
from shogiarena._core.contexts.dashboard.ports.spsa_payloads import SpsaRevisionState
from shogiarena._core.contexts.spsa.adapters.ledger_store import SpsaLedger, open_spsa_ledger
from shogiarena._core.contexts.spsa.application.dashboard.summary_accumulator import SummaryAccumulator
from shogiarena._core.platform.db.store.entities import EngineArtifact, Game, GameInstanceParticipation, Player
from shogiarena._core.platform.db.store.repository import ShogiRepositoryPort
from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_or_none
from shogiarena._core.shared.kernel.json_types import JsonObject


class SpsaRunProjectionError(RuntimeError):
    """Raised when authoritative ledger content cannot be projected safely."""


class SpsaRunProjector:
    """Maintain ledger-derived update, pair, game, event, and summary views."""

    def __init__(self, *, run_dir: Path, db_path: Path, immutable_db: bool) -> None:
        # ``run_dir`` は ledger を開くためだけに使う。artifact(spsa/*.json など)はここから
        # 読まないこと。アーカイブ閲覧では archive snapshot resolver が解決した一時領域が
        # 渡ることがあり、そこには DB しか置かれていない(task 0066)。
        # ``immutable_db`` はアーカイブ閲覧かどうかを表すので、game.db と ledger の両方に
        # 効かせる。ledger だけ ``mode=ro`` に残すと、そこがアーカイブに ``-shm`` を作る。
        self._ledger: SpsaLedger = open_spsa_ledger(run_dir, read_only=True, immutable=immutable_db)
        self._db_path = db_path
        self._immutable_db = immutable_db
        self._lock = threading.RLock()
        self._repository_lock = threading.Lock()
        self._repository: ShogiRepositoryPort | None = None
        self._closed = False
        self._run_id = self._load_run_id()
        self._run_status = self._load_run_status()
        self._revision = 0
        self._generation = 0
        self._data_version = 0
        self._last_update_rowid = 0
        self._last_pair_rowid = 0
        self._last_game_rowid = 0
        self._last_ltc_rowid = 0
        self._update_rows: dict[int, tuple[object, ...]] = {}
        self._pairs_by_update: dict[int, list[JsonObject]] = {}
        self._pairs_by_id: dict[str, JsonObject] = {}
        self._games_by_update: dict[int, list[JsonObject]] = {}
        self._games_by_id: dict[str, JsonObject] = {}
        self._updates: dict[int, JsonObject] = {}
        self._revision_events: list[JsonObject] = []
        self._ltc_results: list[JsonObject] = []
        self._events_cache: list[JsonObject] | None = None
        self._summary = SummaryAccumulator()
        self._summarized_game_ids: set[str] = set()
        self._summarized_updates: set[int] = set()
        self._last_refreshed_updates: tuple[int, ...] = ()
        self._full_rebuild()

    @property
    def revision(self) -> int:
        """Return the latest projected ledger event revision."""

        with self._lock:
            return self._revision

    def revision_state(self) -> SpsaRevisionState:
        """Return the run identity, durable ledger revision, and projected data version.

        ``event_revisions`` は quarantine / LTC 判定 / terminal でしか増えないため、
        durable revision だけを見ていると通常の update commit と対局結果を取りこぼす。
        dashboard の更新契機には ``data_generation`` を使う。
        """

        self.refresh()
        with self._lock:
            return SpsaRevisionState(
                run_id=self._run_id,
                revision=self._revision,
                data_generation=self._generation,
                is_terminal=self._run_status == "terminal",
            )

    @property
    def generation(self) -> int:
        """Return the in-memory projection generation."""

        self.refresh()
        with self._lock:
            return self._generation

    @property
    def last_refreshed_updates(self) -> tuple[int, ...]:
        """Return update indices touched by the latest incremental refresh."""

        with self._lock:
            return self._last_refreshed_updates

    def _load_run_id(self) -> str:
        rows = self._ledger.connection.execute("SELECT run_id FROM run_contract ORDER BY run_id").fetchall()
        if len(rows) != 1:
            raise SpsaRunProjectionError(f"SPSA ledger must contain exactly one run contract; found {len(rows)}")
        return str(rows[0][0])

    def _load_run_status(self) -> str:
        row = self._ledger.connection.execute(
            "SELECT status FROM run_contract WHERE run_id = ?",
            (self._run_id,),
        ).fetchone()
        if row is None or str(row[0]) not in {"initializing", "running", "terminal"}:
            raise SpsaRunProjectionError("SPSA ledger run contract has an invalid status")
        return str(row[0])

    @staticmethod
    def _parse_object(raw: object, *, label: str) -> JsonObject:
        try:
            parsed = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SpsaRunProjectionError(f"Corrupt SPSA ledger JSON for {label}") from exc
        value = coerce_json_object_or_none(parsed)
        if value is None:
            raise SpsaRunProjectionError(f"SPSA ledger JSON for {label} must be an object")
        return value

    @staticmethod
    def _epoch_ms(raw: object, *, label: str) -> int:
        try:
            return int(datetime.fromisoformat(str(raw)).timestamp() * 1000)
        except (TypeError, ValueError) as exc:
            raise SpsaRunProjectionError(f"Corrupt SPSA ledger timestamp for {label}") from exc

    def _load_update_rows(self, *, minimum_rowid: int = 0, active_update_idx: int | None = None) -> set[int]:
        rows = self._ledger.connection.execute(
            """
            SELECT rowid, update_idx, state, theta_before_json, theta_candidate_json,
                   theta_final_json, schedule_json, ltc_required, revision, updated_at
            FROM updates
            WHERE run_id = ? AND rowid > ?
            ORDER BY rowid
            """,
            (self._run_id, minimum_rowid),
        ).fetchall()
        if active_update_idx is not None:
            active_row = self._ledger.connection.execute(
                """
                SELECT rowid, update_idx, state, theta_before_json, theta_candidate_json,
                       theta_final_json, schedule_json, ltc_required, revision, updated_at
                FROM updates
                WHERE run_id = ? AND update_idx = ?
                """,
                (self._run_id, active_update_idx),
            ).fetchone()
            if active_row is not None and all(int(row[0]) != int(active_row[0]) for row in rows):
                rows.append(active_row)
        affected: set[int] = set()
        for row in rows:
            rowid = int(row[0])
            update_idx = int(row[1])
            cached = self._update_rows.get(update_idx)
            normalized = tuple(row[2:])
            if cached != normalized:
                self._update_rows[update_idx] = normalized
                affected.add(update_idx)
            self._last_update_rowid = max(self._last_update_rowid, rowid)
        return affected

    def _load_pair_rows(self, *, minimum_rowid: int = 0) -> set[int]:
        rows = self._ledger.connection.execute(
            """
            SELECT rowid, update_idx, pair_id, assignment_kind, color_assignment_json, created_at
            FROM pair_assignments
            WHERE run_id = ? AND rowid > ?
            ORDER BY rowid
            """,
            (self._run_id, minimum_rowid),
        ).fetchall()
        affected: set[int] = set()
        for rowid, update_idx_raw, pair_id_raw, kind_raw, assignment_raw, created_at in rows:
            update_idx = int(update_idx_raw)
            pair_id = str(pair_id_raw)
            pair: JsonObject = {
                "pair_id": pair_id,
                "assignment_kind": str(kind_raw),
                # update の開始時刻はここでしか分からない。対局の観測時刻は「終わった時刻」
                # なので、着手前に確定する pair の割り当て時刻を開始とみなす。
                "created_at": self._epoch_ms(created_at, label=f"pair {pair_id}"),
                "color_assignment": self._parse_object(
                    assignment_raw,
                    label=f"pair {pair_id} color assignment",
                ),
            }
            self._pairs_by_id[pair_id] = pair
            self._pairs_by_update.setdefault(update_idx, []).append(pair)
            self._last_pair_rowid = max(self._last_pair_rowid, int(rowid))
            affected.add(update_idx)
        return affected

    def _load_game_rows(self, *, minimum_rowid: int = 0) -> set[int]:
        rows = self._ledger.connection.execute(
            """
            SELECT rowid, game_id, update_idx, pair_id, observation_kind,
                   result_kind, game_db_id, observed_at
            FROM game_observations
            WHERE run_id = ? AND rowid > ?
            ORDER BY rowid
            """,
            (self._run_id, minimum_rowid),
        ).fetchall()
        affected: set[int] = set()
        for rowid, game_id_raw, update_idx_raw, pair_id_raw, kind_raw, result_raw, db_id_raw, observed_at in rows:
            game_id = str(game_id_raw)
            update_idx = int(update_idx_raw)
            game: JsonObject = {
                "game_id": game_id,
                "update_idx": update_idx,
                "pair_id": str(pair_id_raw),
                "observation_kind": str(kind_raw),
                "result_kind": str(result_raw),
                "game_db_id": int(db_id_raw),
                "observed_at": str(observed_at),
            }
            self._games_by_id[game_id] = game
            self._games_by_update.setdefault(update_idx, []).append(game)
            self._last_game_rowid = max(self._last_game_rowid, int(rowid))
            affected.add(update_idx)
        return affected

    def _load_revision_rows(self, *, minimum_revision: int = 0) -> set[int]:
        rows = self._ledger.connection.execute(
            """
            SELECT revision, event_type, payload_json, created_at
            FROM event_revisions
            WHERE run_id = ? AND revision > ?
            ORDER BY revision
            """,
            (self._run_id, minimum_revision),
        ).fetchall()
        affected: set[int] = set()
        for revision_raw, event_type_raw, payload_raw, created_at in rows:
            revision = int(revision_raw)
            payload = self._parse_object(payload_raw, label=f"event revision {revision}")
            for key in ("update_idx", "tested_update_idx"):
                value = payload.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    affected.add(value)
            self._revision_events.append(
                {
                    "event": str(event_type_raw),
                    "revision": revision,
                    "ts": self._epoch_ms(created_at, label=f"event revision {revision}"),
                    **payload,
                }
            )
            self._revision = revision
        if rows:
            self._events_cache = None
        return affected

    def _load_ltc_rows(self, *, minimum_rowid: int = 0) -> set[int]:
        rows = self._ledger.connection.execute(
            """
            SELECT rowid, tested_update_idx, baseline_update_idx, decision,
                   evidence_json, final_theta_json, decided_at, revision
            FROM ltc_decisions
            WHERE run_id = ? AND rowid > ?
            ORDER BY rowid
            """,
            (self._run_id, minimum_rowid),
        ).fetchall()
        affected: set[int] = set()
        for (
            rowid,
            update_idx_raw,
            baseline_idx_raw,
            decision_raw,
            evidence_raw,
            theta_raw,
            decided_at,
            revision,
        ) in rows:
            update_idx = int(update_idx_raw)
            evidence = self._parse_object(evidence_raw, label=f"update {update_idx} LTC evidence")
            final_theta = self._parse_object(theta_raw, label=f"update {update_idx} LTC final theta")
            self._ltc_results.append(
                {
                    **evidence,
                    "update_idx": update_idx,
                    "baseline_update_idx": int(baseline_idx_raw) if baseline_idx_raw is not None else None,
                    "decision": str(decision_raw),
                    "final_theta": final_theta,
                    "timestamp": self._epoch_ms(decided_at, label=f"update {update_idx} LTC decision"),
                    "revision": int(revision),
                }
            )
            self._last_ltc_rowid = max(self._last_ltc_rowid, int(rowid))
            affected.add(update_idx)
        return affected

    def _full_rebuild(self) -> None:
        affected = self._load_update_rows()
        affected.update(self._load_pair_rows())
        affected.update(self._load_game_rows())
        affected.update(self._load_ltc_rows())
        affected.update(self._load_revision_rows())
        for update_idx in sorted(affected):
            self._rebuild_update(update_idx)
        self._require_single_pending_update()
        self._last_refreshed_updates = tuple(sorted(affected))
        self._rebuild_summary()
        self._data_version = self._read_data_version()
        self._generation = 1

    def _require_single_pending_update(self) -> None:
        pending = [idx for idx, update in self._updates.items() if update.get("ledger_state") != "COMMITTED"]
        if len(pending) > 1:
            raise SpsaRunProjectionError(f"SPSA ledger has multiple non-committed updates: {pending}")

    def _read_data_version(self) -> int:
        row = self._ledger.connection.execute("PRAGMA data_version").fetchone()
        if row is None:
            raise SpsaRunProjectionError("SPSA ledger data_version is unavailable")
        return int(row[0])

    def _select_theta(self, *, update_idx: int, state: str, row: tuple[object, ...]) -> JsonObject:
        theta_before_raw, theta_candidate_raw, theta_final_raw = row[1], row[2], row[3]
        if state == "COMMITTED":
            if theta_final_raw is None:
                raise SpsaRunProjectionError(f"Committed SPSA update {update_idx} has no final theta")
            return self._parse_object(theta_final_raw, label=f"update {update_idx} final theta")
        if state in {"CANDIDATE_COMPUTED", "LTC_PENDING", "LTC_RUNNING", "ACCEPTED", "REVERTED"}:
            if theta_candidate_raw is None:
                raise SpsaRunProjectionError(f"SPSA update {update_idx} state {state} has no candidate theta")
            return self._parse_object(theta_candidate_raw, label=f"update {update_idx} candidate theta")
        return self._parse_object(theta_before_raw, label=f"update {update_idx} prior theta")

    def _game_event(self, game: JsonObject) -> JsonObject:
        game_id = str(game["game_id"])
        pair_id = str(game["pair_id"])
        pair = self._pairs_by_id.get(pair_id)
        if pair is None:
            raise SpsaRunProjectionError(f"SPSA game {game_id} references missing pair {pair_id}")
        assignment = pair.get("color_assignment")
        if not isinstance(assignment, dict):
            raise SpsaRunProjectionError(f"SPSA pair {pair_id} has no color assignment")
        games = assignment.get("games")
        if not isinstance(games, list):
            raise SpsaRunProjectionError(f"SPSA pair {pair_id} color assignment has no games")
        tuned_as_black: bool | None = None
        for assigned_game in games:
            if isinstance(assigned_game, dict) and assigned_game.get("game_id") == game_id:
                tuned_as = assigned_game.get("tuned_as")
                if tuned_as not in {"black", "white"}:
                    raise SpsaRunProjectionError(f"SPSA game {game_id} has invalid tuned_as assignment")
                tuned_as_black = tuned_as == "black"
                break
        if tuned_as_black is None:
            raise SpsaRunProjectionError(f"SPSA pair {pair_id} does not assign game {game_id}")

        result_kind = str(game["result_kind"])
        winner: int | None
        if result_kind == "DRAW":
            winner = 2
        elif result_kind == "BLACK_WIN":
            winner = 1 if tuned_as_black else 0
        elif result_kind == "WHITE_WIN":
            winner = 0 if tuned_as_black else 1
        elif result_kind in {"INCOMPLETE", "FAILED_OBSERVATION"}:
            winner = None
        else:
            raise SpsaRunProjectionError(f"SPSA game {game_id} has invalid result kind {result_kind}")
        observation_kind = str(game["observation_kind"])
        event_type = "game_result" if winner is not None else "game_observation"
        return {
            "event": event_type,
            "update_idx": int(str(game["update_idx"])),
            "winner": winner,
            "tuned_as_black": tuned_as_black,
            "phase": "ltc" if observation_kind == "LTC" else "tuning",
            "family": observation_kind.lower(),
            "is_ltc": observation_kind == "LTC",
            "game_id": game_id,
            "game_result": result_kind,
            "ts": self._epoch_ms(game["observed_at"], label=f"game {game_id}"),
        }

    def _rebuild_update(self, update_idx: int) -> None:
        row = self._update_rows.get(update_idx)
        if row is None:
            self._updates.pop(update_idx, None)
            return
        state = str(row[0])
        params = self._select_theta(update_idx=update_idx, state=state, row=row)
        theta_before = self._parse_object(row[1], label=f"update {update_idx} prior theta")
        theta_candidate = (
            self._parse_object(row[2], label=f"update {update_idx} candidate theta") if row[2] is not None else None
        )
        if row[4] is None:
            raise SpsaRunProjectionError(f"SPSA update {update_idx} has no durable schedule")
        schedule = self._parse_object(row[4], label=f"update {update_idx} schedule")
        games = self._games_by_update.get(update_idx, [])
        game_events = [self._game_event(game) for game in games]
        # W-D-L は **θ+ 対 θ− の tuning 対局だけ**で数える。
        # LTC 対局の "tuned" は候補 vs 承認済みベースラインという別の比較であり、
        # 混ぜると 1 つの数字が 2 種類の比較を指すことになる。LTC は専用の列で出す。
        played_events = [event for event in game_events if event["event"] == "game_result" and not event.get("is_ltc")]
        wins = sum(event.get("winner") == 1 for event in played_events)
        losses = sum(event.get("winner") == 0 for event in played_events)
        draws = sum(event.get("winner") == 2 for event in played_events)
        started_at, ended_at = self._update_time_span(update_idx, played_events=played_events)
        update_event: JsonObject = {
            "event": "update" if state == "COMMITTED" else "update_pending",
            "update_idx": update_idx,
            "params": params,
            "theta_before": theta_before,
            "theta_candidate": theta_candidate,
            "timestamp": self._epoch_ms(row[7], label=f"update {update_idx}"),
            "is_pending": state != "COMMITTED",
            "ledger_stage": state,
            **schedule,
        }
        self._updates[update_idx] = {
            "run_id": self._run_id,
            "update_idx": update_idx,
            "ledger_state": state,
            "is_pending": state != "COMMITTED",
            "params": params,
            "theta_before": theta_before,
            "theta_candidate": theta_candidate,
            "ltc_required": bool(row[5]),
            "revision": int(str(row[6])),
            "timestamp": update_event["timestamp"],
            "started_at": started_at,
            "ended_at": ended_at,
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "game_ids": [str(game["game_id"]) for game in games if game.get("observation_kind") == "SPSA"],
            "ltc_game_ids": [str(game["game_id"]) for game in games if game.get("observation_kind") == "LTC"],
            **schedule,
        }
        self._events_cache = None

    def _update_time_span(
        self,
        update_idx: int,
        *,
        played_events: Sequence[JsonObject],
    ) -> tuple[int | None, int | None]:
        """Return when an update started and finished, in epoch milliseconds.

        dashboard の Updates テーブルは `started_at` / `ended_at` を読むが、
        ledger の `updates` 行は commit 時刻しか持っていない。開始は pair 割り当ての
        `created_at`、終了は対局観測の最終時刻から導く。

        まだ 1 局も終わっていない update では終了は `None` になる。
        """

        pair_times = [
            value
            for pair in self._pairs_by_update.get(update_idx, [])
            if isinstance(value := pair.get("created_at"), int)
        ]
        game_times = [value for event in played_events if isinstance(value := event.get("ts"), int)]
        started_candidates = pair_times or game_times
        started_at = min(started_candidates) if started_candidates else None
        ended_at = max(game_times) if game_times else None
        return started_at, ended_at

    def _rebuild_summary(self) -> None:
        self._summary = SummaryAccumulator()
        self._summarized_game_ids.clear()
        self._summarized_updates.clear()
        self._extend_summary(set(self._updates))

    def _extend_summary(self, affected: set[int]) -> None:
        for update_idx in sorted(affected):
            for game in self._games_by_update.get(update_idx, []):
                game_id = str(game["game_id"])
                if game_id in self._summarized_game_ids:
                    continue
                self._summary.consume_event(self._game_event(game))
                self._summarized_game_ids.add(game_id)
            update = self._updates.get(update_idx)
            if update is None or update.get("ledger_state") != "COMMITTED" or update_idx in self._summarized_updates:
                continue
            self._summary.consume_event(
                {
                    "event": "update",
                    "update_idx": update_idx,
                    "step": update.get("step"),
                    "delta_norm": update.get("delta_norm"),
                    "ts": update.get("timestamp"),
                }
            )
            self._summarized_updates.add(update_idx)

    def _all_events_without_refresh(self) -> list[JsonObject]:
        if self._events_cache is None:
            events: list[JsonObject] = []
            for update_idx in sorted(self._updates):
                games = self._games_by_update.get(update_idx, [])
                events.extend(self._game_event(game) for game in games)
                update = self._updates[update_idx]
                events.append(
                    {
                        "event": "update" if update["ledger_state"] == "COMMITTED" else "update_pending",
                        "update_idx": update_idx,
                        "params": update["params"],
                        "timestamp": update["timestamp"],
                        "is_pending": update["is_pending"],
                        "ledger_stage": update["ledger_state"],
                        **{
                            key: value
                            for key, value in update.items()
                            if key
                            not in {
                                "run_id",
                                "update_idx",
                                "ledger_state",
                                "is_pending",
                                "params",
                                "theta_before",
                                "theta_candidate",
                                "ltc_required",
                                "revision",
                                "timestamp",
                                "wins",
                                "losses",
                                "draws",
                                "game_ids",
                                "ltc_game_ids",
                            }
                        },
                    }
                )
            events.extend(self._revision_events)
            self._events_cache = sorted(
                events,
                key=lambda event: (
                    int(event.get("ts", event.get("timestamp", 0)))
                    if isinstance(event.get("ts", event.get("timestamp", 0)), int)
                    else 0,
                    str(event.get("event")),
                ),
            )
        return self._events_cache

    def refresh(self) -> int:
        """Refresh only after another connection has committed ledger changes."""

        with self._lock:
            if self._closed:
                raise RuntimeError("SPSA projector is closed")
            data_version = self._read_data_version()
            if data_version == self._data_version:
                self._last_refreshed_updates = ()
                return self._revision

            self._run_status = self._load_run_status()
            pending_updates = [
                idx for idx, update in self._updates.items() if update.get("ledger_state") != "COMMITTED"
            ]
            minimum_pending = max(pending_updates) if pending_updates else None
            affected = self._load_update_rows(
                minimum_rowid=self._last_update_rowid,
                active_update_idx=minimum_pending,
            )
            affected.update(self._load_pair_rows(minimum_rowid=self._last_pair_rowid))
            affected.update(self._load_game_rows(minimum_rowid=self._last_game_rowid))
            affected.update(self._load_ltc_rows(minimum_rowid=self._last_ltc_rowid))
            affected.update(self._load_revision_rows(minimum_revision=self._revision))
            for update_idx in sorted(affected):
                self._rebuild_update(update_idx)
            self._require_single_pending_update()
            if affected:
                self._extend_summary(affected)
                self._generation += 1
            self._data_version = data_version
            self._last_refreshed_updates = tuple(sorted(affected))
            return self._revision

    def pair_ids(self, update_idx: int) -> tuple[str, ...]:
        """Return the projected pair index for one update."""

        self.refresh()
        with self._lock:
            return tuple(str(pair["pair_id"]) for pair in self._pairs_by_update.get(update_idx, []))

    def game_identity(self, game_id: str) -> JsonObject | None:
        """Return a copy of one projected game identity."""

        self.refresh()
        with self._lock:
            game = self._games_by_id.get(game_id)
            return dict(game) if game is not None else None

    def game_snapshot(self, game_id: str) -> JsonObject | None:
        """Return one canonical ledger-derived game event."""

        self.refresh()
        with self._lock:
            game = self._games_by_id.get(game_id)
            return self._game_event(game) if game is not None else None

    def game_snapshots(self, game_ids: Sequence[str]) -> dict[str, JsonObject]:
        """Return canonical ledger-derived game events after one refresh."""

        requested = tuple(dict.fromkeys(game_ids))
        if not requested:
            return {}
        self.refresh()
        with self._lock:
            return {
                game_id: self._game_event(game)
                for game_id in requested
                if (game := self._games_by_id.get(game_id)) is not None
            }

    def game_entries(self) -> list[tuple[str, int]]:
        """Return canonical game IDs and observation timestamps."""

        self.refresh()
        with self._lock:
            return [
                (
                    str(game["game_id"]),
                    self._epoch_ms(game["observed_at"], label=f"game {game['game_id']}"),
                )
                for game in self._games_by_id.values()
            ]

    def load_events(self) -> list[JsonObject]:
        """Return the canonical in-memory event projection."""

        self.refresh()
        with self._lock:
            return [dict(event) for event in self._all_events_without_refresh()]

    def summary_aggregates(self) -> JsonObject:
        """Return canonical summary aggregates derived from ledger rows."""

        self.refresh()
        with self._lock:
            return dict(self._summary.to_dict())

    def operational_status(self) -> JsonObject:
        """Return authoritative run, ledger, LTC, and remote execution status."""

        self.refresh()
        with self._lock:
            contract = self._ledger.connection.execute(
                """
                SELECT contract_schema, resume_hash, space_digest, status
                FROM run_contract WHERE run_id = ?
                """,
                (self._run_id,),
            ).fetchone()
            metadata = self._ledger.connection.execute(
                "SELECT application_schema_id, schema_digest FROM ledger_metadata WHERE singleton_id = 1"
            ).fetchone()
            pending = self._ledger.connection.execute(
                """
                SELECT update_idx, state, revision FROM updates
                WHERE run_id = ? AND state != 'COMMITTED'
                ORDER BY update_idx LIMIT 1
                """,
                (self._run_id,),
            ).fetchone()
            last_committed_row = self._ledger.connection.execute(
                "SELECT max(update_idx) FROM updates WHERE run_id = ? AND state = 'COMMITTED'",
                (self._run_id,),
            ).fetchone()
            terminal = self._ledger.connection.execute(
                """
                SELECT status, reason, last_committed_update, pending_stage, resumable
                FROM terminal_state WHERE run_id = ?
                """,
                (self._run_id,),
            ).fetchone()
            baseline = self._ledger.connection.execute(
                """
                SELECT accepted_update_idx, revision FROM accepted_baseline
                WHERE run_id = ?
                """,
                (self._run_id,),
            ).fetchone()
            decision = self._ledger.connection.execute(
                """
                SELECT tested_update_idx, baseline_update_idx, decision, evidence_digest, revision
                FROM ltc_decisions WHERE run_id = ?
                ORDER BY tested_update_idx DESC LIMIT 1
                """,
                (self._run_id,),
            ).fetchone()
            revision = self._revision

        if contract is None or metadata is None:
            raise SpsaRunProjectionError("SPSA operational projection requires ledger contract metadata")
        last_committed = (
            int(last_committed_row[0]) if last_committed_row is not None and last_committed_row[0] is not None else -1
        )
        completion: JsonObject = (
            {
                "status": str(terminal[0]),
                "termination_reason": str(terminal[1]),
                "last_committed_update": int(terminal[2]),
                "pending_update": last_committed + 1 if terminal[3] is not None else None,
                "pending_stage": None if terminal[3] is None else str(terminal[3]),
                "resumable": bool(terminal[4]),
            }
            if terminal is not None
            else {
                "status": str(contract[3]),
                "termination_reason": None,
                "last_committed_update": last_committed,
                "pending_update": int(pending[0]) if pending is not None else None,
                "pending_stage": str(pending[1]) if pending is not None else None,
                "resumable": None,
            }
        )
        remote, node_multiplier = self._load_latest_remote_status()
        return {
            "run_id": self._run_id,
            "contract": {
                "schema_version": str(contract[0]),
                "resume_hash": str(contract[1]),
                "space_digest": str(contract[2]),
            },
            "completion": completion,
            "accepted_baseline": (
                {
                    "update_idx": int(baseline[0]),
                    "revision": int(baseline[1]),
                }
                if baseline is not None
                else None
            ),
            "last_ltc_decision": (
                {
                    "tested_update_idx": int(decision[0]),
                    "baseline_update_idx": int(decision[1]),
                    "decision": str(decision[2]),
                    "evidence_digest": str(decision[3]),
                    "revision": int(decision[4]),
                }
                if decision is not None
                else None
            ),
            "ledger": {
                "schema_version": str(metadata[0]),
                "schema_digest": str(metadata[1]),
                "revision": revision,
            },
            "remote_execution": remote,
            "node_multiplier": node_multiplier,
        }

    def _load_latest_remote_status(self) -> tuple[JsonObject, JsonObject]:
        repository = self._resolve_repository()
        if repository is None:
            return {"status": "not_observed"}, {"status": "not_applicable", "value": None}
        # `operation()` ends the session boundary, so the next call observes newly written
        # games. The engine and its pool stay alive, which is the expensive part to rebuild.
        with repository.operation() as session:
            row = session.execute(
                select(
                    GameInstanceParticipation.instance_id,
                    GameInstanceParticipation.extra,
                    EngineArtifact.metadata_json,
                )
                .outerjoin(EngineArtifact, GameInstanceParticipation.engine_artifact_id == EngineArtifact.id)
                .where(GameInstanceParticipation.run_id == self._run_id)
                .order_by(GameInstanceParticipation.game_id.desc())
                .limit(1)
            ).first()
        if row is None:
            return {"status": "not_observed"}, {"status": "not_applicable", "value": None}
        extra = coerce_json_object_or_none(row[1]) or {}
        remote = coerce_json_object_or_none(extra.get("remote_execution"))
        if remote is None or not remote:
            return {"status": "not_observed"}, {"status": "not_applicable", "value": None}
        artifact_metadata = coerce_json_object_or_none(row[2]) or {}
        multiplier = remote.get("effective_node_multiplier")
        node_status: JsonObject = (
            {"status": "applied", "value": multiplier}
            if isinstance(multiplier, int | float) and not isinstance(multiplier, bool)
            else {"status": "unknown", "value": None}
        )
        return (
            {
                "status": "observed",
                "instance_id": None if row[0] is None else str(row[0]),
                "endpoint_identity": remote.get("endpoint_identity"),
                "deployment_id": remote.get("deployment_id"),
                "job_id": remote.get("job_id"),
                "participation": {
                    "run_id": remote.get("spsa_run_id"),
                    "update_idx": remote.get("spsa_update_idx"),
                    "pair_id": remote.get("spsa_pair_id"),
                    "attempt_id": remote.get("attempt_id"),
                },
                "engine_digest": artifact_metadata.get("sha256"),
            },
            node_status,
        )

    def load_updates(self) -> list[JsonObject]:
        """Return committed canonical update entries in update order."""

        self.refresh()
        with self._lock:
            return [
                dict(self._updates[idx])
                for idx in sorted(self._updates)
                if self._updates[idx].get("ledger_state") == "COMMITTED"
            ]

    def load_ltc_results(self) -> list[JsonObject]:
        """Return canonical LTC decision evidence."""

        self.refresh()
        with self._lock:
            return [dict(result) for result in self._ltc_results]

    def load_update(self, update_idx: int) -> JsonObject | None:
        """Return a copy of one canonical update."""

        self.refresh()
        with self._lock:
            update = self._updates.get(update_idx)
            return dict(update) if update is not None else None

    def _resolve_repository(self) -> ShogiRepositoryPort | None:
        """Return the projector-owned game database repository, opening it once.

        ``open_dashboard_repository`` は呼び出しごとに SQLAlchemy engine を作るため、
        summary のように高頻度で叩かれる経路では engine 生成が支配的コストになる。
        engine は使い回し、session だけを呼び出しごとに閉じて読み取りを新鮮に保つ。
        """

        with self._repository_lock:
            if self._repository is not None:
                return self._repository
            repository = open_dashboard_repository(self._db_path, immutable=self._immutable_db)
            if repository is None:
                # The database may not exist yet; retry on the next call.
                return None
            self._repository = repository
            return repository

    def load_game_records(self, game_ids: list[str]) -> dict[str, JsonObject]:
        """Batch-hydrate requested game metadata with one database query."""

        requested = list(dict.fromkeys(game_ids))
        if not requested:
            return {}
        repository = self._resolve_repository()
        if repository is None:
            return {}
        black = aliased(Player)
        white = aliased(Player)
        with repository.operation() as session:
            rows = session.execute(
                select(
                    Game.game_name,
                    black.player_name,
                    white.player_name,
                    Game.game_result,
                    Game.num_moves,
                    Game.start_date,
                    Game.end_date,
                )
                .join(black, Game.black_player)
                .join(white, Game.white_player)
                .where(Game.game_name.in_(requested))
            ).all()
        return {
            str(game_name): {
                "game_id": str(game_name),
                "black_player": str(black_player),
                "white_player": str(white_player),
                "game_result": str(game_result),
                "num_moves": int(num_moves),
                "start_time": start_date.isoformat() if start_date is not None else None,
                "end_time": end_date.isoformat() if end_date is not None else None,
                "status": "completed",
            }
            for game_name, black_player, white_player, game_result, num_moves, start_date, end_date in rows
        }

    def close(self) -> None:
        """Release the instance-owned ledger connection and game database repository."""

        with self._lock:
            if self._closed:
                return
            self._ledger.close()
            self._closed = True
        with self._repository_lock:
            repository = self._repository
            self._repository = None
        if repository is not None:
            repository.close_db()


__all__ = ["SpsaRunProjectionError", "SpsaRunProjector"]
