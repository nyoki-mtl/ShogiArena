from __future__ import annotations

from pathlib import Path

import rshogi
from rshogi.core import Board, Move

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
    for move in board.legal_moves_full():
        mv = move.to_move()
        if mv.to_usi() == usi:
            return mv
    raise AssertionError(f"move not found: {usi}")


def _sample_record() -> rshogi.record.GameRecord:
    board = Board()
    board.set_sfen(_STARTPOS)

    move1 = _legal_move(board, "7g7f")
    board.apply_move(move1)
    move2 = _legal_move(board, "3c3d")

    return rshogi.record.GameRecord.from_dict(
        {
            "metadata": {
                "black_player": "black",
                "white_player": "white",
                "attributes": {"game_name": "g", "game_type": "arena"},
            },
            "init_position_sfen": _STARTPOS,
            "moves": [
                {"move": int(move1), "engine_info": {"eval": 123}},
                {"move": int(move2), "engine_info": {"eval": -35}},
            ],
            "result": {"result": GameResult.BLACK_WIN.name, "ply_count": 2},
        }
    )


def _sample_psv_record() -> rshogi.record.GameRecord:
    return rshogi.record.GameRecord.from_dict(
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
    assert payload[:4] == b"SBIN"

    decoded = reader.deserialize(payload)
    decoded_dict = decoded.to_dict()
    decoded_moves = decoded_dict.get("moves", [])
    assert isinstance(decoded_moves, list)

    assert int(decoded.result) == int(record.result)
    assert len(decoded.moves) == len(record.moves)
    decoded_evals = tuple(
        move.get("engine_info", {}).get("eval") if isinstance(move, dict) else None for move in decoded_moves
    )
    record_moves = record.to_dict().get("moves", [])
    assert isinstance(record_moves, list)
    record_evals = tuple(
        move.get("engine_info", {}).get("eval") if isinstance(move, dict) else None for move in record_moves
    )
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
    writer.append_record(rec)
    writer.append_record(rec)
    writer.close()

    files = sorted(out_dir.glob("*.sbinpack"))
    assert len(files) == 2


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
