from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import (
    EngineConfig,
)
from shogiarena._core.contexts.game_session.adapters.orchestration.participation_records import (
    collect_participation_records_remote_pair,
)
from shogiarena._core.contexts.instances.application.instance_models import (
    InstanceConfig,
    InstanceType,
)
from shogiarena._core.contexts.instances.application.instance_pool import InstancePool
from tests.unit.test_remote_job_store import _spec


def test_remote_participation_contains_execution_and_worker_provenance() -> None:
    pool = InstancePool()
    pool.add_instance(
        InstanceConfig(
            name="worker",
            type=InstanceType.SSH,
            engine_dir="",
            host="worker",
            slots=4,
            tags=["spsa"],
        )
    )
    orchestrator = SimpleNamespace(
        instance_pool=pool,
        session_context=SimpleNamespace(run_id="run"),
    )
    spec = _spec()
    started_at = datetime.now(UTC)
    completed_at = started_at + timedelta(seconds=2)
    remote_execution = {
        "endpoint_identity": "ssh://worker#host-key",
        "deployment_id": "d" * 64,
        "job_id": "job-" + "1" * 32,
        "attempt_id": "attempt-" + "2" * 32,
        "execution_digest": spec.execution_digest,
        "spsa_run_id": "run",
        "spsa_update_idx": 3,
        "spsa_pair_id": "update-3",
    }

    records = collect_participation_records_remote_pair(
        orchestrator,
        spec_payload=spec.model_dump(mode="json"),
        black_engine_name="black",
        white_engine_name="white",
        black_spec=EngineConfig(name="black", engine_path=None, artifact="black"),
        white_spec=EngineConfig(name="white", engine_path=None, artifact="white"),
        black_pool_key="black#tuned",
        white_pool_key="white#baseline",
        instance_id="worker",
        started_at=started_at,
        completed_at=completed_at,
        remote_execution=remote_execution,
        worker_provenance={
            "engine_identity": {
                "black": {"name": "Black Engine", "author": "A"},
                "white": {"name": "White Engine", "author": "B"},
            },
            "timestamps": {
                "started_at": started_at.isoformat(),
                "finished_at": completed_at.isoformat(),
            },
        },
    )

    assert len(records) == 2
    for record in records:
        assert record.instance is not None
        assert record.instance.instance_id == "worker"
        assert record.engine_artifact is not None
        assert record.engine_artifact.metadata is not None
        assert record.engine_artifact.metadata["sha256"] in {"a" * 64, "b" * 64}
        assert record.started_at != record.completed_at
        assert record.extra is not None
        assert record.extra["fixed_option_evidence_scope"] == "remote_runtime"
        file_set = record.extra["fixed_option_file_set"]
        assert file_set["scope"] == "remote_runtime"
        assert file_set["status"] == "inventory_only"
        assert file_set["engine_artifact"]["sha256"] in {"a" * 64, "b" * 64}
        assert record.extra["remote_execution"]["job_id"] == remote_execution["job_id"]
        assert record.extra["remote_execution"]["spsa_update_idx"] == 3
        assert "worker_execution_timestamps" in record.extra["remote_execution"]
        assert "engine_info" in record.extra
