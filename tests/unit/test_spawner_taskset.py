from shogiarena._core.contexts.instances.application import engine_process_spawner as spawner


def test_wrap_with_taskset_without_affinity() -> None:
    result = spawner.wrap_with_taskset("engine_cmd", None)
    assert result == "exec engine_cmd"


def test_wrap_with_taskset_with_affinity() -> None:
    result = spawner.wrap_with_taskset("engine_cmd", (0, 2, 3))
    assert "taskset -c 0,2,3 engine_cmd" in result
    assert "taskset not found" in result
    assert result.startswith("if command -v taskset")
