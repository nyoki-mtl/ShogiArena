from shogiarena.arena.orchestrators.engine_pool import EnginePool


def test_engine_pool_slot_key():
    assert EnginePool.slot_key("engine", None) == "engine@auto"
    assert EnginePool.slot_key("engine", "local") == "engine@local"
    assert EnginePool.slot_key("engine", "  local ") == "engine@local"
    assert EnginePool.slot_key("engine", "") == "engine@auto"
