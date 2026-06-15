"""`EngineRuntimeFactory._determine_instance` と既定ローカルプールの挙動を検証する。"""

from __future__ import annotations

import pytest

from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from shogiarena._core.platform.engine_provisioning.runtime_factory import EngineRuntimeFactory


def test_determine_instance_requires_pool() -> None:
    """プール未注入時は合成インスタンスを返さず fail-fast する。"""
    with pytest.raises(ValueError, match="instance_pool is required"):
        EngineRuntimeFactory._determine_instance(None, None)


def test_determine_instance_id_requires_pool() -> None:
    with pytest.raises(ValueError, match="instance_id requires an instance_pool"):
        EngineRuntimeFactory._determine_instance("worker-1", None)


def test_ensure_default_local_pool_yields_real_local_instance() -> None:
    """既定ローカルプールは本物のローカル ``Instance`` を生成できる。"""
    pool = InstancePool.ensure_default_local_pool()
    instance = pool.ensure_local_instance()
    assert instance.is_local
    # _determine_instance はこのプールから同じローカルインスタンスを返す。
    assert EngineRuntimeFactory._determine_instance(None, pool) is instance
