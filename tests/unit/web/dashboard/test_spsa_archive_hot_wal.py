"""checkpoint されていない WAL を持つアーカイブを、原本を変えずに完全に読めること。

`immutable=1` は WAL を無視するので、crash した run のアーカイブでは commit 済みの対局が
無音で欠ける(task 0066)。ここでは欠陥そのもの(制御)と修正後の挙動を対にして固定する。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from shutil import copyfile

import pytest
from aiohttp.test_utils import TestClient, TestServer

from shogiarena._core.contexts.dashboard.adapters.archive_snapshot import resolve_archive_databases
from shogiarena._core.contexts.dashboard.ports.archive_snapshot_ports import GAME_DB_RELATIVE_PATH
from shogiarena._core.contexts.spsa.adapters.ledger_store import open_spsa_ledger
from shogiarena._core.contexts.spsa.ports.ledger_ports import SPSA_LEDGER_RELATIVE_PATH
from shogiarena._core.interfaces.composition_root.default_root import build_default_root
from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory

GAME_COUNT = 12


def _tree_identity(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _write_hot_wal_spsa_archive(root: Path) -> Path:
    """全対局が un-checkpointed WAL にある SPSA アーカイブを作る。"""

    archive = root / "archive"
    archive.mkdir(parents=True, exist_ok=True)

    source = root / "source.db"
    repository = SQLiteShogiDBFactory(source).create()
    repository.create_tables()
    repository.close_db()

    connection = sqlite3.connect(source, isolation_level=None)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("INSERT INTO player (game_type, player_name) VALUES ('arena', 'plus')")
        connection.execute("INSERT INTO player (game_type, player_name) VALUES ('arena', 'minus')")
        for index in range(GAME_COUNT):
            connection.execute(
                """
                INSERT INTO game (
                    game_type, game_name, game_result, num_moves,
                    black_player_id, white_player_id, initial_position_sfen, end_date, updated_date
                ) VALUES ('arena', ?, 'BLACK_WIN', 1, 1, 2, 'startpos', ?, ?)
                """,
                (f"game-{index}", f"2026-01-01 00:00:{index:02d}", f"2026-01-01 00:00:{index:02d}"),
            )
        # ``-wal`` を開いたまま複製することで、writer を kill したのと同じディスク状態を
        # 決定的に作る(``test_release_db_compat_production.py`` と同じ手法)。
        for suffix in ("", "-wal", "-shm"):
            copyfile(Path(f"{source}{suffix}"), Path(f"{archive / GAME_DB_RELATIVE_PATH}{suffix}"))
    finally:
        connection.close()

    with open_spsa_ledger(archive) as ledger:
        ledger.connection.execute(
            """
            INSERT INTO run_contract (
                run_id, contract_schema, resume_hash, space_digest, rng_schema,
                sealed_run_seed, status, contract_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("run-1", "v1", "resume", "space", "rng", "seed", "running", "{}", "t0", "t0"),
        )
        ledger.connection.commit()
    (archive / "spsa" / "meta.json").write_text(
        json.dumps(
            {
                "type": "spsa",
                "experiment_name": "hot-wal",
                "session_uuid": "session-1",
                "num_updates": 1,
                "initial_params": {"p": 1.0},
            }
        ),
        encoding="utf-8",
    )
    return archive


def _make_ledger_wal_hot(archive: Path, *, update_count: int) -> None:
    """ledger の update を un-checkpointed WAL にだけ持たせる。

    `wal_autocheckpoint=0` で書き、``-wal`` を開いたまま family を上書き複製することで
    「commit 済みだが本体には無い」状態を決定的に作る。
    """

    ledger_path = archive / SPSA_LEDGER_RELATIVE_PATH
    staging = archive.parent / "ledger-staging.sqlite3"
    copyfile(ledger_path, staging)
    connection = sqlite3.connect(staging, isolation_level=None)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for index in range(1, update_count + 1):
            connection.execute(
                """
                INSERT INTO updates (
                    run_id, update_idx, state, theta_before_json, theta_candidate_json,
                    theta_final_json, schedule_json, ltc_required, revision, created_at, updated_at
                ) VALUES (?, ?, 'COMMITTED', '{}', '{}', '{}', '{}', 0, 0, ?, ?)
                """,
                ("run-1", index, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            )
        for suffix in ("", "-wal", "-shm"):
            source = Path(f"{staging}{suffix}")
            if suffix and not source.exists():
                continue
            copyfile(source, Path(f"{ledger_path}{suffix}"))
    finally:
        connection.close()


async def _fetch_totals(*, db_path: Path, run_dir: Path, ledger_run_dir: Path) -> tuple[int, int]:
    server = build_default_root().api_server_factory(
        db_path=db_path,
        port=8080,
        run_dir=run_dir,
        instance_pool=None,
        read_only=True,
        ledger_run_dir=ledger_run_dir,
        dashboard_num_workers=1,
        dashboard_profiles=("spsa",),
    )
    async with TestClient(TestServer(server.app)) as client:
        games = await client.get("/api/games?limit=100&offset=0")
        assert games.status == 200
        games_payload = await games.json()
        updates = await client.get("/api/spsa/updates?limit=100&offset=0")
        assert updates.status == 200
        updates_payload = await updates.json()
    return int(games_payload["total"]), int(updates_payload["total"])


async def _fetch_game_total(*, db_path: Path, run_dir: Path, ledger_run_dir: Path) -> int:
    game_total, _ = await _fetch_totals(db_path=db_path, run_dir=run_dir, ledger_run_dir=ledger_run_dir)
    return game_total


@pytest.mark.asyncio
async def test_immutable_reads_alone_hide_games_committed_into_the_wal(tmp_path: Path) -> None:
    """resolver を外すと壊れることを固定する。

    アーカイブ閲覧は全経路 ``immutable=1`` で読む(そうしないと原本に sidecar を作る)。
    その前提のもとでは、resolver を通さない DB path は commit 済みの対局を無音で落とす。
    **resolver は最適化ではなく、immutable 読みの前提条件である。**
    """

    archive = _write_hot_wal_spsa_archive(tmp_path)

    total = await _fetch_game_total(
        db_path=archive / GAME_DB_RELATIVE_PATH,
        run_dir=archive,
        ledger_run_dir=archive,
    )

    assert total == 0


@pytest.mark.asyncio
async def test_resolved_archive_serves_every_committed_game(tmp_path: Path) -> None:
    archive = _write_hot_wal_spsa_archive(tmp_path)
    before = _tree_identity(archive)

    with resolve_archive_databases(
        archive,
        relative_database_paths=[GAME_DB_RELATIVE_PATH, SPSA_LEDGER_RELATIVE_PATH],
    ) as snapshot:
        total = await _fetch_game_total(
            db_path=snapshot.database_path_for(GAME_DB_RELATIVE_PATH),
            run_dir=archive,
            ledger_run_dir=snapshot.database_dir_for(SPSA_LEDGER_RELATIVE_PATH),
        )

    assert total == GAME_COUNT
    assert _tree_identity(archive) == before


UPDATE_COUNT = 7


@pytest.mark.asyncio
async def test_immutable_reads_alone_hide_updates_committed_into_the_ledger_wal(tmp_path: Path) -> None:
    """ledger 側の欠陥も固定する。resolver を通さないと update が 0 件になる。"""

    archive = _write_hot_wal_spsa_archive(tmp_path)
    _make_ledger_wal_hot(archive, update_count=UPDATE_COUNT)

    _, update_total = await _fetch_totals(
        db_path=archive / GAME_DB_RELATIVE_PATH,
        run_dir=archive,
        ledger_run_dir=archive,
    )

    assert update_total == 0


@pytest.mark.asyncio
async def test_resolved_snapshot_serves_a_ledger_whose_wal_is_hot(tmp_path: Path) -> None:
    """hot な ledger が materialize され、projector が temp 側の ledger を開くこと。

    今回新設した `ledger_run_dir` 配線の、実際に発動する側の分岐。
    """

    archive = _write_hot_wal_spsa_archive(tmp_path)
    _make_ledger_wal_hot(archive, update_count=UPDATE_COUNT)
    before = _tree_identity(archive)

    with resolve_archive_databases(
        archive,
        relative_database_paths=[GAME_DB_RELATIVE_PATH, SPSA_LEDGER_RELATIVE_PATH],
    ) as snapshot:
        assert snapshot.materialized_database_paths == (GAME_DB_RELATIVE_PATH, SPSA_LEDGER_RELATIVE_PATH)
        ledger_dir = snapshot.database_dir_for(SPSA_LEDGER_RELATIVE_PATH)
        assert ledger_dir != archive
        game_total, update_total = await _fetch_totals(
            db_path=snapshot.database_path_for(GAME_DB_RELATIVE_PATH),
            run_dir=archive,
            ledger_run_dir=ledger_dir,
        )

    assert game_total == GAME_COUNT
    assert update_total == UPDATE_COUNT
    assert _tree_identity(archive) == before


@pytest.mark.asyncio
async def test_serving_an_archive_creates_no_ledger_sidecars(tmp_path: Path) -> None:
    """ledger の read-only open もアーカイブに ``-shm`` を作らないこと。

    実 run のスモークで最初に見つかった漏れ。game.db 側だけを直しても、ledger が
    ``mode=ro`` のままだとそこがツリーを書き換える。
    """

    archive = _write_hot_wal_spsa_archive(tmp_path)
    ledger_path = archive / SPSA_LEDGER_RELATIVE_PATH
    # 実 run のアーカイブと同じ形にする: 1.2.3 以降 ledger は run 中 WAL なので、
    # sidecar が残っていなくても header の journal mode は ``wal`` のままになる。
    # この状態を `mode=ro` で開くと `-wal` と `-shm` が新規作成される。
    marking = sqlite3.connect(ledger_path)
    assert str(marking.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower() == "wal"
    marking.close()
    ledger_shm = Path(f"{ledger_path}-shm")
    ledger_shm.unlink(missing_ok=True)
    Path(f"{ledger_path}-wal").unlink(missing_ok=True)
    before = _tree_identity(archive)

    with resolve_archive_databases(
        archive,
        relative_database_paths=[GAME_DB_RELATIVE_PATH, SPSA_LEDGER_RELATIVE_PATH],
    ) as snapshot:
        # ledger は冷えているので原本を指す。そこを読んでもツリーは変わってはいけない。
        assert snapshot.database_dir_for(SPSA_LEDGER_RELATIVE_PATH) == archive
        await _fetch_game_total(
            db_path=snapshot.database_path_for(GAME_DB_RELATIVE_PATH),
            run_dir=archive,
            ledger_run_dir=snapshot.database_dir_for(SPSA_LEDGER_RELATIVE_PATH),
        )

    assert not ledger_shm.exists()
    assert _tree_identity(archive) == before


@pytest.mark.asyncio
async def test_serving_a_clean_archive_creates_no_sidecars(tmp_path: Path) -> None:
    """`mode=ro` は clean な WAL アーカイブにも sidecar を作る。それを止める。

    止めないと、次回起動時に resolver が自分で作った sidecar を検出し、clean な
    アーカイブが serve のたびに複製される自己汚染ループになる。
    """

    archive = _write_hot_wal_spsa_archive(tmp_path)
    game_db = archive / GAME_DB_RELATIVE_PATH
    # WAL を畳んで clean なアーカイブにする(sidecar は残らない)。
    folding = sqlite3.connect(game_db)
    folding.execute("PRAGMA journal_mode=DELETE")
    folding.commit()
    folding.close()
    for suffix in ("-wal", "-shm"):
        Path(f"{game_db}{suffix}").unlink(missing_ok=True)
    # journal mode を WAL へ戻す(sidecar 無し・WAL mode という実アーカイブの形)。
    restoring = sqlite3.connect(game_db)
    restoring.execute("PRAGMA journal_mode=WAL")
    restoring.close()
    Path(f"{game_db}-wal").unlink(missing_ok=True)
    Path(f"{game_db}-shm").unlink(missing_ok=True)
    before = _tree_identity(archive)

    with resolve_archive_databases(
        archive,
        relative_database_paths=[GAME_DB_RELATIVE_PATH, SPSA_LEDGER_RELATIVE_PATH],
    ) as snapshot:
        assert snapshot.materialized_database_paths == ()
        total = await _fetch_game_total(
            db_path=snapshot.database_path_for(GAME_DB_RELATIVE_PATH),
            run_dir=archive,
            ledger_run_dir=snapshot.database_dir_for(SPSA_LEDGER_RELATIVE_PATH),
        )

    assert total == GAME_COUNT
    assert _tree_identity(archive) == before
