from __future__ import annotations

from shogiarena._core.contexts.spsa.application.dashboard.summary_accumulator import SummaryAccumulator
from shogiarena._core.contexts.spsa.application.dashboard.summary_cache_state import (
    SummaryCacheState,
    create_empty_summary_cache_state,
    refresh_summary_cache_state_from_events,
)


def test_summary_cache_state_rebuilds_from_authoritative_events(tmp_path) -> None:
    events_path = tmp_path / "events.jsonl"
    state = SummaryCacheState(
        aggregates=SummaryAccumulator.from_dict(
            {
                "wins": 3,
                "losses": 1,
                "updates_seen": [1, 2],
                "step_history": [0.1, 0.2],
            }
        ),
        events_offset=100,
        events_size=100,
        events_mtime_ns=30,
    )
    events_path.write_text(
        '{"event":"game_result","session_uuid":"session-2","winner":1}\n',
        encoding="utf-8",
    )

    assert refresh_summary_cache_state_from_events(
        state,
        events_path=events_path,
    )
    assert state.aggregates.wins == 1
    assert state.aggregates.losses == 0
    assert state.aggregates.updates_seen == set()


def test_partial_jsonl_tail_is_consumed_once_after_newline_completion(tmp_path) -> None:
    events_path = tmp_path / "events.jsonl"
    state = create_empty_summary_cache_state()
    complete = b'{"event":"update","update_idx":1}\n'
    partial = b'{"event":"update","update_idx":2,"note":"'
    utf8_bytes = "棋".encode()
    events_path.write_bytes(complete + partial + utf8_bytes[:2])

    assert refresh_summary_cache_state_from_events(
        state,
        events_path=events_path,
    )
    partial_offset = len(complete)
    assert state.events_offset == partial_offset
    assert state.aggregates.updates_seen == {1}

    with events_path.open("ab") as handle:
        handle.write(utf8_bytes[2:] + b'"}\n')

    assert refresh_summary_cache_state_from_events(
        state,
        events_path=events_path,
    )
    assert state.events_offset == events_path.stat().st_size
    assert state.aggregates.updates_seen == {1, 2}

    assert not refresh_summary_cache_state_from_events(
        state,
        events_path=events_path,
    )
    assert state.aggregates.updates_seen == {1, 2}


def test_long_event_history_applies_only_appended_projection(tmp_path) -> None:
    events_path = tmp_path / "events.jsonl"
    state = create_empty_summary_cache_state()
    historical_event = b'{"event":"game_result","winner":1}\n'
    events_path.write_bytes(historical_event * 10_000)

    assert refresh_summary_cache_state_from_events(state, events_path=events_path)
    historical_offset = state.events_offset
    assert historical_offset == events_path.stat().st_size
    assert state.aggregates.wins == 10_000

    with events_path.open("ab") as handle:
        handle.write(b'{"event":"game_result","winner":0}\n')

    assert refresh_summary_cache_state_from_events(state, events_path=events_path)
    assert state.events_offset > historical_offset
    assert state.aggregates.wins == 10_000
    assert state.aggregates.losses == 1
