from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import make_mocked_request

from shogiarena._core.contexts.csa_watch.application.run_watcher import RunView
from shogiarena._core.contexts.csa_watch.domain.run_state import RunState
from shogiarena._core.interfaces.dashboard.csa.api import CsaAPI


@pytest.mark.asyncio
async def test_wire_tail_read_is_offloaded_and_starts_on_a_line_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "1800000000"
    event_path = tmp_path / f"{run_id}-events.jsonl"
    event_path.write_text("{}\n", encoding="utf-8")
    wire_path = tmp_path / f"{run_id}-wire.log"
    wire_path.write_text("first-line\nsecond-line\nthird-line\n", encoding="utf-8")
    view = RunView(worker_idx=0, state=RunState(run_id=run_id), path=event_path)
    api = CsaAPI(views_supplier=lambda: (view,))
    calls: list[tuple[Any, tuple[Any, ...]]] = []

    async def immediate_to_thread(function: Any, *args: Any) -> Any:
        calls.append((function, args))
        return function(*args)

    monkeypatch.setattr("shogiarena._core.interfaces.dashboard.csa.api.asyncio.to_thread", immediate_to_thread)
    request = make_mocked_request(
        "GET",
        f"/api/csa/runs/{run_id}/wire?tail=18",
        match_info={"run_id": run_id},
    )

    response = await api.get_wire_log(request)

    assert len(calls) == 1
    assert response.body.decode("utf-8").splitlines() == ["third-line"]
