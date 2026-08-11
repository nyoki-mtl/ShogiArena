from __future__ import annotations

import json
from pathlib import Path

import pytest
import rsshogi
from rsshogi.core import Board, Move

from shogiarena._core.contexts.game_session.adapters.orchestration.config_engine import RecordOutputConfig
from shogiarena._core.platform.records.binary_writer import (
    RecordBinaryWriter,
    RecordBinaryWriterConfig,
)
from shogiarena._core.platform.records.codecs import (
    get_reader,
    get_serializer,
    get_stream_exporter,
    iter_psv_entries,
)
from shogiarena._core.shared.kernel.game_results import GameResult

_STARTPOS = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1"


def _legal_move(board: Board, usi: str) -> Move:
    for move in board.legal_moves_move32():
        mv = move.to_move()
        if mv.to_usi() == usi:
            return mv
    raise AssertionError(f"move not found: {usi}")


def _sample_record() -> rsshogi.record.Record:
    board = Board()
    board.set_sfen(_STARTPOS)

    move1 = _legal_move(board, "7g7f")
    board.apply_move(move1)
    move2 = _legal_move(board, "3c3d")

    return rsshogi.record.Record.from_dict(
        {
            "metadata": {
                "black_player": "black",
                "white_player": "white",
                "attributes": {"game_name": "g", "game_type": "arena"},
            },
            "init_position_sfen": _STARTPOS,
            "moves": [
                {"move": int(move1), "engine_info": {"eval": 123}},
                {"move": int(move2), "engine_info": {"eval": 120}},
            ],
            "result": {"result": GameResult.BLACK_WIN.name, "ply_count": 2},
        }
    )


def _sample_psv_record() -> rsshogi.record.Record:
    return rsshogi.record.Record.from_dict(
        {
            "metadata": {
                "black_player": "black",
                "white_player": "white",
                "attributes": {"game_name": "g_psv", "game_type": "arena"},
            },
            "init_position_sfen": _STARTPOS,
            "moves": [{"move": "7g7f", "engine_info": {"eval": 42}}],
            "result": {"result": GameResult.BLACK_WIN.name, "ply_count": 1},
        }
    )


def _evals(record: rsshogi.record.Record) -> tuple[int | None, ...]:
    moves = record.to_dict().get("moves", [])
    assert isinstance(moves, list)
    evals: list[int | None] = []
    for move in moves:
        engine_info = move.get("engine_info") if isinstance(move, dict) else None
        value = engine_info.get("eval") if isinstance(engine_info, dict) else None
        evals.append(value if isinstance(value, int) else None)
    return tuple(evals)


def test_record_output_config_supports_sbinpack_and_rejects_pack() -> None:
    cfg = RecordOutputConfig(format="sbinpack", max_positions_per_file=1024, output_dir=Path("."))
    assert cfg.format == "sbinpack"

    try:
        RecordOutputConfig(format="pack", max_positions_per_file=1024)
    except ValueError as exc:
        assert "'psv' or 'sbinpack'" in str(exc)
    else:
        raise AssertionError("pack should be rejected")


def test_serializer_formats_include_sbinpack() -> None:
    assert get_serializer("sbinpack") is not None


def test_sbinpack_roundtrip() -> None:
    serializer = get_serializer("sbinpack")
    reader = get_reader("sbinpack")
    assert serializer is not None
    assert reader is not None

    record = _sample_record()
    payload = serializer.serialize(record)
    assert isinstance(payload, bytes)
    # rsshogi 1.0.0 emits sbinpack v2 ("SBN2"); v1 ("SBIN") is no longer produced.
    assert payload[:4] == b"SBN2"

    decoded = reader.deserialize(payload)
    assert int(decoded.result) == int(record.result)
    assert len(decoded.moves) == len(record.moves)
    decoded_evals = _evals(decoded)
    record_evals = _evals(record)
    assert record_evals == (123, 120)
    assert decoded_evals == record_evals


def test_sbinpack_binary_writer_rotates_by_max_games(tmp_path: Path) -> None:
    out_dir = tmp_path / "records"
    writer = RecordBinaryWriter(
        RecordBinaryWriterConfig(
            format_id="sbinpack",
            output_dir=out_dir,
            max_positions_per_file=999999,
            max_games_per_file=1,
            file_prefix="games",
        )
    )

    rec = _sample_record()
    writer.append_record(rec, game_id="g1")
    writer.append_record(rec, game_id="g2")
    writer.close()

    files = sorted(out_dir.glob("*.sbinpack"))
    assert len(files) == 2
    index_entries = [
        json.loads(line)
        for line in (out_dir / "records_index.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [entry["game_id"] for entry in index_entries] == ["g1", "g2"]
    manifest = json.loads((out_dir / "records_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["totals"]["games"] == 2


def test_sfen_serializer_is_not_registered() -> None:
    assert get_serializer("sfen") is None


def test_psv_is_stream_exporter_only() -> None:
    exporter = get_stream_exporter("psv")
    assert exporter is not None

    reader = get_reader("psv")
    assert reader is None

    one_move_entries = list(exporter.export(_sample_psv_record()))
    all_moves_entries = list(exporter.export(_sample_record()))
    assert len(one_move_entries) > 0
    assert len(all_moves_entries) > len(one_move_entries)


def test_psv_binary_writer_appends_record(tmp_path: Path) -> None:
    out_dir = tmp_path / "psv_records"
    writer = RecordBinaryWriter(
        RecordBinaryWriterConfig(
            format_id="psv",
            output_dir=out_dir,
            max_positions_per_file=999999,
            max_games_per_file=None,
            file_prefix="games",
        )
    )

    record = _sample_record()
    expected_payload = b"".join(iter_psv_entries(record))

    writer.append_record(record)
    writer.close()

    files = sorted(out_dir.glob("*.psv"))
    assert len(files) == 1
    assert files[0].stat().st_size > 0
    assert files[0].read_bytes() == expected_payload


def test_binary_writer_skips_already_indexed_game_id(tmp_path: Path) -> None:
    out_dir = tmp_path / "records"
    record = _sample_record()
    writer = RecordBinaryWriter(
        RecordBinaryWriterConfig(
            format_id="sbinpack",
            output_dir=out_dir,
            max_positions_per_file=999999,
            max_games_per_file=None,
            file_prefix="games",
        )
    )
    writer.append_record(record, game_id="game-1", game_type="generate")
    writer.close()

    resumed = RecordBinaryWriter(
        RecordBinaryWriterConfig(
            format_id="sbinpack",
            output_dir=out_dir,
            max_positions_per_file=999999,
            max_games_per_file=None,
            file_prefix="games",
        )
    )
    resumed.append_record(record, game_id="game-1", game_type="generate")
    resumed.close()

    index_lines = [line for line in (out_dir / "records_index.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(index_lines) == 1
    assert resumed.get_records_summary()["total_games"] == 1


def test_binary_writer_truncates_unindexed_tail_bytes_on_resume(tmp_path: Path) -> None:
    out_dir = tmp_path / "records"
    record = _sample_record()
    writer = RecordBinaryWriter(
        RecordBinaryWriterConfig(
            format_id="sbinpack",
            output_dir=out_dir,
            max_positions_per_file=999999,
            max_games_per_file=None,
            file_prefix="games",
        )
    )
    writer.append_record(record, game_id="game-1", game_type="generate")
    writer.close()

    data_file = next(out_dir.glob("*.sbinpack"))
    indexed_size = data_file.stat().st_size
    with data_file.open("ab") as handle:
        handle.write(b"unindexed-tail")
    assert data_file.stat().st_size > indexed_size

    resumed = RecordBinaryWriter(
        RecordBinaryWriterConfig(
            format_id="sbinpack",
            output_dir=out_dir,
            max_positions_per_file=999999,
            max_games_per_file=None,
            file_prefix="games",
        )
    )
    resumed.close()

    assert data_file.stat().st_size == indexed_size


def _record_without_eval() -> rsshogi.record.Record:
    return rsshogi.record.Record.from_dict(
        {
            "metadata": {
                "black_player": "black",
                "white_player": "white",
                "attributes": {"game_name": "g_noeval", "game_type": "arena"},
            },
            "init_position_sfen": _STARTPOS,
            "moves": [{"move": "7g7f", "engine_info": {}}],
            "result": {"result": GameResult.BLACK_WIN.name, "ply_count": 1},
        }
    )


def test_iter_psv_entries_requires_eval() -> None:
    # The psv path now pre-validates evals (mirroring sbinpack) with a clear, indexed message.
    with pytest.raises(ValueError, match="psv serialization requires eval"):
        list(iter_psv_entries(_record_without_eval()))


def test_append_after_close_is_rejected(tmp_path: Path) -> None:
    out_dir = tmp_path / "records"
    writer = RecordBinaryWriter(
        RecordBinaryWriterConfig(
            format_id="sbinpack",
            output_dir=out_dir,
            max_positions_per_file=1,
            max_games_per_file=None,
            file_prefix="games",
        )
    )
    writer.append_record(_sample_record(), game_id="g1")
    writer.close()
    # After close a rotation-triggering append must be rejected, not silently reopen a new file.
    with pytest.raises(RuntimeError, match="closed"):
        writer.append_record(_sample_record(), game_id="g2")


def test_sbinpack_oversized_record_does_not_rotate_empty_file(tmp_path: Path) -> None:
    out_dir = tmp_path / "records"
    writer = RecordBinaryWriter(
        RecordBinaryWriterConfig(
            format_id="sbinpack",
            output_dir=out_dir,
            max_positions_per_file=1,  # the sample record has 2 moves, exceeding the limit
            max_games_per_file=None,
            file_prefix="games",
        )
    )
    writer.append_record(_sample_record(), game_id="g1")
    writer.close()
    # An oversized record on a fresh (empty) file must be written in place rather than rotating
    # and leaving a stray empty file behind.
    files = sorted(out_dir.glob("*.sbinpack"))
    assert len(files) == 1


def _single_file_config(out_dir: Path) -> RecordBinaryWriterConfig:
    return RecordBinaryWriterConfig(
        format_id="sbinpack",
        output_dir=out_dir,
        max_positions_per_file=999999,
        max_games_per_file=None,
        file_prefix="games",
    )


def _index_game_ids(out_dir: Path) -> list[str]:
    lines = (out_dir / "records_index.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["game_id"] for line in lines if line.strip()]


def test_recover_drops_torn_final_index_line(tmp_path: Path) -> None:
    # R5: a crash mid-append can leave a truncated final index line; reopen must tolerate it.
    out_dir = tmp_path / "records"
    config = _single_file_config(out_dir)
    writer = RecordBinaryWriter(config)
    writer.append_record(_sample_record(), game_id="g1")
    writer.append_record(_sample_record(), game_id="g2")
    writer.close()

    with (out_dir / "records_index.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"game_id": "g3", "byte_en')  # torn JSON, no newline

    reopened = RecordBinaryWriter(config)
    assert reopened.written_game_ids() == {"g1", "g2"}
    reopened.close()
    assert _index_game_ids(out_dir) == ["g1", "g2"]


def test_recover_drops_index_entry_beyond_truncated_binary(tmp_path: Path) -> None:
    # R1b: if the binary tail is lost but the index entry survived, reopen must drop the entry
    # referencing the missing bytes instead of later reading corrupt data.
    out_dir = tmp_path / "records"
    config = _single_file_config(out_dir)
    writer = RecordBinaryWriter(config)
    writer.append_record(_sample_record(), game_id="g1")
    writer.append_record(_sample_record(), game_id="g2")
    writer.close()

    lines = (out_dir / "records_index.jsonl").read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines if line.strip()]
    assert entries[0]["file"] == entries[1]["file"]  # both games in one file
    binary_path = out_dir / entries[1]["file"]
    with binary_path.open("r+b") as handle:
        handle.truncate(entries[0]["byte_end"])  # drop g2's bytes

    reopened = RecordBinaryWriter(config)
    assert reopened.written_game_ids() == {"g1"}
    reopened.close()
    assert _index_game_ids(out_dir) == ["g1"]


def test_recover_rebuilds_corrupt_manifest_from_index(tmp_path: Path) -> None:
    # R6: the index is the source of truth; a corrupt manifest must be rebuilt, not block resume.
    out_dir = tmp_path / "records"
    config = _single_file_config(out_dir)
    writer = RecordBinaryWriter(config)
    writer.append_record(_sample_record(), game_id="g1")
    writer.close()

    (out_dir / "records_manifest.json").write_text("{ not valid json", encoding="utf-8")

    reopened = RecordBinaryWriter(config)
    assert reopened.written_game_ids() == {"g1"}
    reopened.close()

    manifest = json.loads((out_dir / "records_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["totals"]["games"] == 1


def test_recover_rejects_mismatched_format(tmp_path: Path) -> None:
    # Opening an existing sbinpack output as psv must fail, not mix incompatible records.
    out_dir = tmp_path / "records"
    writer = RecordBinaryWriter(_single_file_config(out_dir))
    writer.append_record(_sample_record(), game_id="g1")
    writer.close()

    psv_config = RecordBinaryWriterConfig(
        format_id="psv",
        output_dir=out_dir,
        max_positions_per_file=999999,
        max_games_per_file=None,
        file_prefix="games",
    )
    with pytest.raises(ValueError, match="format"):
        RecordBinaryWriter(psv_config)


def test_recover_rejects_mismatched_file_prefix(tmp_path: Path) -> None:
    out_dir = tmp_path / "records"
    writer = RecordBinaryWriter(_single_file_config(out_dir))
    writer.append_record(_sample_record(), game_id="g1")
    writer.close()

    other_prefix = RecordBinaryWriterConfig(
        format_id="sbinpack",
        output_dir=out_dir,
        max_positions_per_file=999999,
        max_games_per_file=None,
        file_prefix="other",
    )
    with pytest.raises(ValueError, match="file_prefix"):
        RecordBinaryWriter(other_prefix)


def test_complete_corrupt_final_index_line_raises(tmp_path: Path) -> None:
    # A fully written (newline-terminated) but corrupt final line is corruption, not a torn append,
    # so it must raise rather than be silently dropped.
    out_dir = tmp_path / "records"
    config = _single_file_config(out_dir)
    writer = RecordBinaryWriter(config)
    writer.append_record(_sample_record(), game_id="g1")
    writer.close()

    with (out_dir / "records_index.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{ this is broken json }\n")  # complete line (trailing newline)

    with pytest.raises(ValueError, match="not valid JSON"):
        RecordBinaryWriter(config)
