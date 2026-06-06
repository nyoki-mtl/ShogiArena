from __future__ import annotations

from shogiarena._core.platform.engine_runtime.usi_engine_session_models import UsiEngineStartError


def test_usi_engine_start_error_exposes_diagnostic_payload() -> None:
    error = UsiEngineStartError(
        engine_name="engine-a",
        engine_path="/tmp/engine-a",
        working_directory="/tmp",
        command=("/tmp/engine-a", "--usi"),
        options={"EvalDir": "/tmp/eval", "Threads": 1},
        failure_phase="isready",
        reason=TimeoutError("ready timeout"),
    )

    payload = error.diagnostic_payload()

    assert payload["engine"] == "engine-a"
    assert payload["executable"] == "/tmp/engine-a"
    assert payload["working_directory"] == "/tmp"
    assert payload["command"] == ["/tmp/engine-a", "--usi"]
    assert payload["options"] == {"EvalDir": "/tmp/eval", "Threads": 1}
    assert payload["failure_phase"] == "isready"
    assert payload["exception_class"] == "TimeoutError"
