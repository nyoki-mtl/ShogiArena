from datetime import UTC, datetime

from shogiarena._core.shared.kernel.participation_records import (
    EngineArtifactSnapshot,
    GameParticipationRecord,
    InstanceSnapshot,
    deserialize_participation_records,
    serialize_participation_records,
)


def _make_participation_record() -> GameParticipationRecord:
    return GameParticipationRecord(
        role="black",
        engine_name="engine-a",
        engine_display_name="Engine A",
        engine_artifact=EngineArtifactSnapshot(
            logical_name="engine-a",
            artifact="owner/abcdef0",
            binary_path="/opt/engine-a",
            build_flags={"avx2": True},
            metadata={"source": "artifact-resolver"},
        ),
        instance=InstanceSnapshot(
            instance_id="local",
            display_name="local",
            instance_type="local",
            tags=("x86_64",),
            extra={"slots": 1},
        ),
        binary_path="/opt/engine-a",
        build_flags={"avx2": True},
        started_at=datetime(2026, 2, 16, 1, 2, 3, tzinfo=UTC),
        completed_at=datetime(2026, 2, 16, 1, 3, 4, tzinfo=UTC),
        run_id="run-001",
        extra={"pool_key": "engine-a#black"},
    )


def test_serialize_deserialize_participation_roundtrip() -> None:
    record = _make_participation_record()

    encoded = serialize_participation_records([record])
    decoded = deserialize_participation_records(encoded)

    assert len(decoded) == 1
    restored = decoded[0]
    assert restored.role == record.role
    assert restored.engine_name == record.engine_name
    assert restored.started_at == record.started_at
    assert restored.completed_at == record.completed_at
    assert restored.engine_artifact is not None
    assert restored.engine_artifact.logical_name == "engine-a"
    assert restored.instance is not None
    assert restored.instance.instance_id == "local"


def test_deserialize_participation_handles_invalid_payload() -> None:
    assert deserialize_participation_records("not-json") == ()
    assert deserialize_participation_records('{"invalid": true}') == ()
    assert deserialize_participation_records('[{"role":"black"}]') == ()


def test_deserialize_participation_accepts_missing_optional_fields() -> None:
    raw = '[{"role":"black","engine_name":"engine-a"}]'
    decoded = deserialize_participation_records(raw)
    assert len(decoded) == 1
    assert decoded[0].role == "black"
    assert decoded[0].engine_name == "engine-a"
    assert decoded[0].engine_display_name is None
    assert decoded[0].started_at is None


def test_deserialize_participation_invalid_datetime_becomes_none() -> None:
    raw = """
    [
      {
        "role": "black",
        "engine_name": "engine-a",
        "engine_display_name": null,
        "engine_artifact": null,
        "instance": null,
        "binary_path": null,
        "build_flags": null,
        "started_at": "invalid-datetime",
        "completed_at": "2026-02-16T01:03:04+00:00",
        "run_id": null,
        "extra": null
      }
    ]
    """
    decoded = deserialize_participation_records(raw)
    assert len(decoded) == 1
    assert decoded[0].started_at is None
    assert decoded[0].completed_at == datetime(2026, 2, 16, 1, 3, 4, tzinfo=UTC)


def test_deserialize_participation_accepts_nested_book_extra() -> None:
    raw = """
    [
      {
        "role": "black",
        "engine_name": "engine-a",
        "extra": {
          "book": {
            "resolved_path": "/srv/book/user_book1.db",
            "fingerprint": {
              "basename": "user_book1.db",
              "hash_method": "size_partial_sha256",
              "partial_sha256": "abc"
            },
            "options": {"BookFile": "user_book1.db", "BookMoves": 16}
          }
        }
      }
    ]
    """
    decoded = deserialize_participation_records(raw)

    assert len(decoded) == 1
    assert decoded[0].extra is not None
    book = decoded[0].extra["book"]
    assert isinstance(book, dict)
    assert book["fingerprint"] == {
        "basename": "user_book1.db",
        "hash_method": "size_partial_sha256",
        "partial_sha256": "abc",
    }
