from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from shogiarena._core.contexts.game_session.application.sprt_service import Sprt
from shogiarena._core.contexts.tournament.application.runner_state import TournamentRunnerState
from shogiarena._core.contexts.tournament.application.session.state_store import TournamentSessionStateStore
from shogiarena._core.contexts.tournament.domain.tournament_models import GameSpec
from shogiarena._core.contexts.tournament.ports.session_state_runtime import (
    AssignmentOverridePayload,
    TournamentOpenBenchStatePort,
    TournamentScheduleGeneratorPort,
    TournamentStateSaveContext,
    TournamentStateSetupContext,
)
from shogiarena._core.shared.kernel.json_types import JsonObject, JsonValue


def _make_game(game_id: str, *, round_num: int = 0) -> GameSpec:
    return GameSpec(
        black_engine="EngineA",
        white_engine="EngineB",
        initial_sfen="startpos",
        game_id=game_id,
        round_num=round_num,
    )


def _make_config(*, seed: str = "1234", schedule_hash: str = "schedule-hash", resume_hash: str = "resume-hash") -> Any:
    return SimpleNamespace(
        experiment_name="state-store-test",
        engines=[SimpleNamespace(name="EngineA"), SimpleNamespace(name="EngineB")],
        tournament=SimpleNamespace(
            game_order="round_robin",
            scheduler="round_robin",
            games_per_pair=2,
            baseline_count=1,
            seed=seed,
        ),
        rules=SimpleNamespace(
            initial_positions=SimpleNamespace(
                flip_policy="none",
                generate=lambda count, generated_seed: [f"startpos-{generated_seed}"] * count,
            )
        ),
        sprt=SimpleNamespace(model_dump=lambda *, mode: {"elo0": 0.0, "elo1": 5.0, "alpha": 0.05, "beta": 0.05}),
        openbench=None,
        records_output=None,
        get_schedule_hash=lambda: schedule_hash,
        get_resume_hash=lambda: resume_hash,
    )


class _SchedulerStub:
    def __init__(self, schedule: list[GameSpec]) -> None:
        self._schedule = list(schedule)
        self.calls: list[dict[str, object]] = []

    def generate_schedule(
        self,
        engines: list[object],
        games_per_pair: int,
        seed: str,
        initial_positions: object,
    ) -> list[GameSpec]:
        self.calls.append(
            {
                "engines": engines,
                "games_per_pair": games_per_pair,
                "seed": seed,
                "initial_positions": initial_positions,
            }
        )
        return list(self._schedule)


class _DbStub:
    def __init__(self, game_names: Iterable[str]) -> None:
        self._game_names = list(game_names)
        self.game_types: list[str] = []

    def get_games_with_players(self, *, game_type: str) -> list[dict[str, object]]:
        self.game_types.append(game_type)
        return [{"game_name": game_name} for game_name in self._game_names]

    def load_record(self, *, game_id: int | None = None, game_name: str | None = None) -> None:
        return None


class _OpenBenchStub:
    def __init__(self, snapshot: JsonObject | None = None) -> None:
        self._snapshot = snapshot
        self.restored: dict[str, JsonValue] | None = None

    def snapshot_state(self) -> JsonObject | None:
        return self._snapshot

    def restore_state(self, openbench_state: dict[str, JsonValue]) -> None:
        self.restored = dict(openbench_state)


def _build_save_context(
    *,
    run_dir: Path,
    config: Any,
    state: TournamentRunnerState,
    openbench: _OpenBenchStub,
    schedule_hash: str = "schedule-hash",
    resume_hash: str = "resume-hash",
    is_generate_run: bool = False,
) -> TournamentStateSaveContext:
    return TournamentStateSaveContext(
        run_dir=run_dir,
        config=config,
        state=state,
        openbench=cast(TournamentOpenBenchStatePort, openbench),
        build_rules_payload=lambda: {"board": "standard"},
        is_generate_run=lambda: is_generate_run,
        serialize_assignment_override=_serialize_assignment_override,
        shared_override_label=_shared_override_label,
        schedule_hash=schedule_hash,
        resume_hash=resume_hash,
    )


def _write_sealed_manifest(run_dir: Path, *, resume_hash: str = "resume-hash") -> None:
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "provenance_sealed",
                "hashes": {"resume_hash": resume_hash},
            }
        ),
        encoding="utf-8",
    )


def _shared_override_label(spec: GameSpec) -> str | None:
    if spec.assigned_instance_black is not None and spec.assigned_instance_black == spec.assigned_instance_white:
        return spec.assigned_instance_black
    return None


def _serialize_assignment_override(spec: GameSpec) -> JsonObject | None:
    black = spec.assigned_instance_black
    white = spec.assigned_instance_white
    if black is None and white is None and not spec.should_require_install:
        return None

    payload: JsonObject = {
        "black": black,
        "white": white,
        "should_require_install": spec.should_require_install,
    }
    if black is not None and black == white:
        payload["shared"] = black
        payload["mode"] = "shared"
    else:
        payload["mode"] = "per_color"
    return payload


def _apply_assignment_override(spec: GameSpec, payload: AssignmentOverridePayload) -> None:
    if payload is None:
        return
    if isinstance(payload, str):
        spec.assigned_instance_black = payload
        spec.assigned_instance_white = payload
        spec.should_require_install = False
        return

    shared = payload.get("shared")
    spec.assigned_instance_black = payload.get("black") or shared
    spec.assigned_instance_white = payload.get("white") or shared
    spec.should_require_install = bool(payload.get("should_require_install", False))


@pytest.mark.asyncio
async def test_try_setup_tournament_creates_schedule_and_persists_run_state(tmp_path: Path) -> None:
    schedule = [_make_game("g001", round_num=1), _make_game("g002", round_num=2)]
    scheduler = _SchedulerStub(schedule)
    openbench = _OpenBenchStub(
        snapshot={
            "submitted": {
                "losses": 0,
                "draws": 0,
                "wins": 0,
                "ll": 0,
                "ld": 0,
                "dd": 0,
                "dw": 0,
                "ww": 0,
                "crashes": 0,
                "timelosses": 0,
                "illegals": 0,
            },
            "last_synced_games": 0,
            "target_test_id": 42,
            "claimed_test_id": None,
            "result_id": None,
            "blacklist": [],
        }
    )
    state = TournamentRunnerState(
        completed_game_summaries={
            "stale": {
                "game_result": "BLACK_WIN",
                "total_plies": 1,
                "start_time": "2026-03-08T00:00:00+00:00",
                "end_time": "2026-03-08T00:00:01+00:00",
            }
        }
    )
    config = _make_config()
    events: list[tuple[str, object]] = []

    def _reset_schedule_tracking() -> None:
        state.cancelled_game_ids.clear()
        state.cancelled_specs.clear()
        state.original_total_games = len(state.game_schedule)
        state.game_display_order = {spec.game_id: index for index, spec in enumerate(state.game_schedule, start=1)}
        events.append(("reset", len(state.game_schedule)))

    def _write_schedule_file(schedule_to_write: list[GameSpec]) -> None:
        payload = [spec.game_id for spec in schedule_to_write]
        events.append(("write", payload))
        (tmp_path / "schedule.json").write_text(json.dumps(payload), encoding="utf-8")

    def _notify_schedule_available() -> None:
        events.append(("notify", None))

    ctx = TournamentStateSetupContext(
        run_dir=tmp_path,
        run_options=SimpleNamespace(should_skip_resume=False),
        config=config,
        scheduler=cast(TournamentScheduleGeneratorPort, scheduler),
        state=state,
        openbench=cast(TournamentOpenBenchStatePort, openbench),
        db_service=None,
        reorder_and_shuffle=lambda games: list(reversed(games)),
        reset_schedule_tracking=_reset_schedule_tracking,
        write_schedule_file=_write_schedule_file,
        notify_schedule_available=_notify_schedule_available,
        reset_display_order=lambda: None,
        apply_assignment_override=_apply_assignment_override,
        ensure_display_order_for_specs=lambda specs: None,
        refresh_game_assignments=lambda: None,
        build_save_context=lambda: _build_save_context(
            run_dir=tmp_path,
            config=config,
            state=state,
            openbench=openbench,
        ),
        schedule_hash="schedule-hash",
        resume_hash="resume-hash",
    )

    store = TournamentSessionStateStore()
    resumed = await store.try_setup_tournament(ctx)

    assert resumed is False
    assert [spec.game_id for spec in state.game_schedule] == ["g002", "g001"]
    assert state.completed_game_summaries == {}
    assert state.original_total_games == 2
    assert events == [
        ("reset", 2),
        ("write", ["g002", "g001"]),
        ("notify", None),
    ]

    run_state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert run_state["schedule_hash"] == "schedule-hash"
    assert run_state["resume_hash"] == "resume-hash"
    assert run_state["total_games"] == 2
    assert run_state["game_display_order"] == {"g002": 1, "g001": 2}
    assert run_state["openbench_state"]["target_test_id"] == 42


@pytest.mark.asyncio
async def test_try_setup_tournament_resumes_existing_run_state_round_trip(tmp_path: Path) -> None:
    active_spec = _make_game("g001", round_num=1)
    cancelled_spec = _make_game("g002", round_num=2)
    cancelled_spec.assigned_instance_black = "worker-2"
    cancelled_spec.assigned_instance_white = "worker-2"
    cancelled_spec.should_require_install = True

    config = _make_config(seed="9876", schedule_hash="schedule-hash", resume_hash="resume-hash")
    saved_openbench = _OpenBenchStub(
        snapshot={
            "submitted": {
                "losses": 2,
                "draws": 1,
                "wins": 0,
                "ll": 0,
                "ld": 0,
                "dd": 0,
                "dw": 0,
                "ww": 0,
                "crashes": 0,
                "timelosses": 0,
                "illegals": 0,
            },
            "last_synced_games": 3,
            "target_test_id": 12,
            "claimed_test_id": None,
            "result_id": None,
            "blacklist": [],
        }
    )
    saved_sprt = Sprt(0.0, 5.0)
    saved_sprt.wins = 1
    saved_sprt.games_played = 1
    saved_sprt.llr = 0.25

    saved_state = TournamentRunnerState(
        game_schedule=[active_spec, cancelled_spec],
        completed_game_ids={"g001"},
        completed_game_summaries={
            "g001": {
                "game_result": "BLACK_WIN",
                "total_plies": 42,
                "start_time": "2026-03-08T00:00:00+00:00",
                "end_time": "2026-03-08T00:01:00+00:00",
            }
        },
        cancelled_game_ids={"g002"},
        cancelled_specs={"g002": cancelled_spec},
        game_display_order={"g001": 1, "g002": 2},
        original_total_games=2,
        sprt=saved_sprt,
    )

    store = TournamentSessionStateStore()
    _write_sealed_manifest(tmp_path)
    store.save_run_state(
        _build_save_context(run_dir=tmp_path, config=config, state=saved_state, openbench=saved_openbench)
    )

    resumed_state = TournamentRunnerState(sprt=Sprt(0.0, 5.0))
    scheduler = _SchedulerStub([_make_game("g001", round_num=1), _make_game("g002", round_num=2)])
    restored_openbench = _OpenBenchStub()
    callback_events: list[str] = []

    def _ensure_display_order_for_specs(specs: Iterable[GameSpec]) -> None:
        next_index = max(resumed_state.game_display_order.values(), default=0) + 1
        for spec in specs:
            if spec.game_id not in resumed_state.game_display_order:
                resumed_state.game_display_order[spec.game_id] = next_index
                next_index += 1
        callback_events.append("ensure_display_order")

    ctx = TournamentStateSetupContext(
        run_dir=tmp_path,
        run_options=SimpleNamespace(should_skip_resume=False),
        config=config,
        scheduler=cast(TournamentScheduleGeneratorPort, scheduler),
        state=resumed_state,
        openbench=cast(TournamentOpenBenchStatePort, restored_openbench),
        db_service=cast(Any, _DbStub(["g001"])),
        reorder_and_shuffle=lambda games: games,
        reset_schedule_tracking=lambda: callback_events.append("reset"),
        write_schedule_file=lambda schedule_to_write: callback_events.append("write"),
        notify_schedule_available=lambda: callback_events.append("notify"),
        reset_display_order=lambda: callback_events.append("reset_display_order"),
        apply_assignment_override=_apply_assignment_override,
        ensure_display_order_for_specs=_ensure_display_order_for_specs,
        refresh_game_assignments=lambda: callback_events.append("refresh"),
        build_save_context=lambda: _build_save_context(
            run_dir=tmp_path,
            config=config,
            state=resumed_state,
            openbench=restored_openbench,
        ),
        schedule_hash="schedule-hash",
        resume_hash="resume-hash",
    )

    resumed = await store.try_setup_tournament(ctx)

    assert resumed is True
    assert [spec.game_id for spec in resumed_state.game_schedule] == ["g001"]
    assert resumed_state.cancelled_game_ids == {"g002"}
    assert resumed_state.cancelled_specs["g002"].assigned_instance_black == "worker-2"
    assert resumed_state.cancelled_specs["g002"].assigned_instance_white == "worker-2"
    assert resumed_state.cancelled_specs["g002"].should_require_install is True
    assert resumed_state.completed_game_ids == {"g001"}
    assert resumed_state.completed_game_summaries == {}
    assert resumed_state.game_display_order == {"g001": 1, "g002": 2}
    assert resumed_state.original_total_games == 2
    assert isinstance(resumed_state.sprt, Sprt)
    assert resumed_state.sprt.games_played == 1
    assert resumed_state.sprt.wins == 1
    assert restored_openbench.restored == saved_openbench.snapshot_state()
    assert callback_events == ["ensure_display_order", "ensure_display_order", "refresh", "notify"]


@pytest.mark.asyncio
async def test_try_setup_tournament_resumes_generate_games_by_generate_game_type(tmp_path: Path) -> None:
    config = _make_config(seed="9876", schedule_hash="schedule-hash", resume_hash="resume-hash")
    saved_state = TournamentRunnerState(game_schedule=[_make_game("g001"), _make_game("g002")])
    openbench = _OpenBenchStub()
    store = TournamentSessionStateStore()
    _write_sealed_manifest(tmp_path)
    store.save_run_state(
        _build_save_context(
            run_dir=tmp_path,
            config=config,
            state=saved_state,
            openbench=openbench,
            is_generate_run=True,
        )
    )

    resumed_state = TournamentRunnerState()
    db = _DbStub(["g001"])
    ctx = TournamentStateSetupContext(
        run_dir=tmp_path,
        run_options=SimpleNamespace(should_skip_resume=False),
        config=config,
        scheduler=cast(TournamentScheduleGeneratorPort, _SchedulerStub([_make_game("g001"), _make_game("g002")])),
        state=resumed_state,
        openbench=cast(TournamentOpenBenchStatePort, openbench),
        db_service=cast(Any, db),
        reorder_and_shuffle=lambda games: games,
        reset_schedule_tracking=lambda: None,
        write_schedule_file=lambda schedule_to_write: None,
        notify_schedule_available=lambda: None,
        reset_display_order=lambda: None,
        apply_assignment_override=_apply_assignment_override,
        ensure_display_order_for_specs=lambda specs: None,
        refresh_game_assignments=lambda: None,
        build_save_context=lambda: _build_save_context(
            run_dir=tmp_path,
            config=config,
            state=resumed_state,
            openbench=openbench,
            is_generate_run=True,
        ),
        schedule_hash="schedule-hash",
        resume_hash="resume-hash",
    )

    resumed = await store.try_setup_tournament(ctx)

    assert resumed is True
    assert resumed_state.completed_game_ids == {"g001"}
    assert db.game_types == ["generate"]


@pytest.mark.asyncio
async def test_try_setup_tournament_rejects_unsealed_manifest(tmp_path: Path) -> None:
    config = _make_config()
    state = TournamentRunnerState()
    saved_state = TournamentRunnerState(game_schedule=[_make_game("g001")])
    openbench = _OpenBenchStub()
    store = TournamentSessionStateStore()
    store.save_run_state(_build_save_context(run_dir=tmp_path, config=config, state=saved_state, openbench=openbench))
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema_version": 2, "status": "inputs_only", "hashes": {"resume_hash": None}}),
        encoding="utf-8",
    )

    ctx = TournamentStateSetupContext(
        run_dir=tmp_path,
        run_options=SimpleNamespace(should_skip_resume=False),
        config=config,
        scheduler=cast(TournamentScheduleGeneratorPort, _SchedulerStub([_make_game("g001")])),
        state=state,
        openbench=cast(TournamentOpenBenchStatePort, openbench),
        db_service=cast(Any, _DbStub(["g001"])),
        reorder_and_shuffle=lambda games: games,
        reset_schedule_tracking=lambda: None,
        write_schedule_file=lambda schedule_to_write: None,
        notify_schedule_available=lambda: None,
        reset_display_order=lambda: None,
        apply_assignment_override=_apply_assignment_override,
        ensure_display_order_for_specs=lambda specs: None,
        refresh_game_assignments=lambda: None,
        build_save_context=lambda: _build_save_context(
            run_dir=tmp_path,
            config=config,
            state=state,
            openbench=openbench,
        ),
        schedule_hash="schedule-hash",
        resume_hash="resume-hash",
    )

    resumed = await store.try_setup_tournament(ctx)

    assert resumed is False
    assert state.game_schedule == []


def test_load_completed_game_ids_fails_fast_on_db_error() -> None:
    # game.db is the authoritative source of completed games on resume; a read failure must fail
    # closed rather than be treated as "zero completed" (which would re-run every game).
    class _RaisingDb:
        def get_games_with_players(self, *, game_type: str) -> list[dict[str, object]]:
            raise RuntimeError("database is locked")

    ctx = SimpleNamespace(
        db_service=cast(Any, _RaisingDb()),
        build_save_context=lambda: SimpleNamespace(is_generate_run=lambda: False),
    )
    with pytest.raises(RuntimeError, match="no-resume"):
        TournamentSessionStateStore._load_completed_game_ids(cast(Any, ctx))
