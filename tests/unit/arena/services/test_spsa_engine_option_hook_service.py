from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from shogiarena._core.contexts.game_session.application.orchestration.engine_option_hook_service import (
    SpsaEngineOptionHookRequest,
    apply_engine_option_hooks,
)
from shogiarena._core.contexts.instances.ports.orchestrator_primitives import make_role_pool_key
from shogiarena._core.shared.kernel.json_types import JsonValue


@dataclass(eq=False)
class _Engine:
    applied_options: list[Mapping[str, JsonValue]] = field(default_factory=list)
    apply_flags: list[tuple[bool, str]] = field(default_factory=list)

    async def apply_engine_options(
        self,
        options: Mapping[str, JsonValue],
        *,
        clear_hash: bool = True,
        after_setoption: str = "isready",
    ) -> None:
        self.applied_options.append(dict(options))
        self.apply_flags.append((clear_hash, after_setoption))


@pytest.mark.asyncio
async def test_apply_and_collect_names_applies_options_for_both_roles() -> None:
    tuned = _Engine()
    baseline = _Engine()
    tuned_key = make_role_pool_key("tuned", "tuned")
    baseline_key = make_role_pool_key("base", "baseline")

    names = await apply_engine_option_hooks(
        engines_by_key={
            tuned_key: tuned,
            baseline_key: baseline,
        },
        request=SpsaEngineOptionHookRequest(
            tuned_pool_key=tuned_key,
            baseline_pool_key=baseline_key,
            tuned_options={"P": 1},
            baseline_options={"Q": 2},
            tuned_label="tuned-label",
            baseline_label="base-label",
        ),
    )

    assert tuned.applied_options == [{"P": 1}]
    assert baseline.applied_options == [{"Q": 2}]
    assert tuned.apply_flags == [(True, "isready")]
    assert baseline.apply_flags == [(True, "isready")]
    assert names[tuned_key] == "tuned-label"
    assert names[baseline_key] == "base-label"


@pytest.mark.asyncio
async def test_apply_and_collect_names_skips_empty_baseline_options() -> None:
    tuned = _Engine()
    baseline = _Engine()
    tuned_key = make_role_pool_key("tuned", "tuned")
    baseline_key = make_role_pool_key("base", "baseline")

    names = await apply_engine_option_hooks(
        engines_by_key={
            tuned_key: tuned,
            baseline_key: baseline,
        },
        request=SpsaEngineOptionHookRequest(
            tuned_pool_key=tuned_key,
            baseline_pool_key=baseline_key,
            tuned_options={"P": 1},
            baseline_options={},
            tuned_label="tuned-label",
            baseline_label="base-label",
        ),
    )

    assert tuned.applied_options == [{"P": 1}]
    assert baseline.applied_options == []
    assert tuned.apply_flags == [(True, "isready")]
    assert names[baseline_key] == "base-label"


@pytest.mark.asyncio
async def test_apply_and_collect_names_forwards_apply_policy() -> None:
    tuned = _Engine()
    tuned_key = make_role_pool_key("tuned", "tuned")

    await apply_engine_option_hooks(
        engines_by_key={tuned_key: tuned},
        request=SpsaEngineOptionHookRequest(
            tuned_pool_key=tuned_key,
            baseline_pool_key=make_role_pool_key("base", "baseline"),
            tuned_options={"P": 1},
            baseline_options={},
            tuned_label="tuned-label",
            baseline_label="base-label",
            clear_hash=False,
            after_setoption="none",
        ),
    )

    assert tuned.apply_flags == [(False, "none")]
