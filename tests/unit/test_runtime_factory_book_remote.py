"""remote への内蔵定跡(A) 配布（book FILE 単体・content-hash 転送）の単体テスト。Task 0017。"""

from __future__ import annotations

from pathlib import Path

import pytest

from shogiarena._core.contexts.instances.application.instance_config_models import InstanceConfig, InstanceType
from shogiarena._core.contexts.instances.application.instance_models import Instance
from shogiarena._core.platform.engine_provisioning.runtime_factory import EngineRuntimeFactory


class _FakeSupport:
    def __init__(self) -> None:
        self.file_transfers: list[tuple[Path, str]] = []
        self.dir_transfers: list[tuple[Path, str]] = []

    def detect_remote_target_cpu(self, instance: object) -> str:
        return "ZEN3"

    async def ensure_remote_binary(self, instance: object, local_binary: Path, remote_binary: str) -> None:
        return None

    def build_local_manifest(self, path: Path) -> dict[str, object]:
        return {"digest": "deadbeefcafef00d"}

    def file_sha256(self, path: Path) -> str:
        return "0123456789abcdef" * 4

    async def ensure_remote_dir_by_manifest(self, instance: object, local_dir: Path, remote_dir: str) -> None:
        self.dir_transfers.append((local_dir, remote_dir))

    async def ensure_remote_file(self, instance: object, local_file: Path, remote_file: str) -> None:
        self.file_transfers.append((local_file, remote_file))


def _make_factory(support: _FakeSupport) -> EngineRuntimeFactory:
    def _unused(*args: object, **kwargs: object) -> object:
        raise AssertionError("factory callable should not be invoked in this test")

    return EngineRuntimeFactory(
        process_spawner=_unused,
        support=support,
        engine_config_factory=_unused,
        mapping_config_factory=_unused,
        engine_session_factory=_unused,
    )


def _ssh_instance(tmp_path: Path) -> Instance:
    config = InstanceConfig(name="remote", type=InstanceType.SSH, engine_dir="", project_root=str(tmp_path / "proj"))
    return Instance(config=config)


@pytest.mark.asyncio
async def test_enabled_book_transfers_file_not_whole_dir(tmp_path: Path) -> None:
    support = _FakeSupport()
    factory = _make_factory(support)
    instance = _ssh_instance(tmp_path)

    book_dir = tmp_path / "books"
    book_dir.mkdir()
    (book_dir / "user_book1.db").write_bytes(b"#YANEURAOU-DB2016 1.00\n")

    options = {"BookDir": str(book_dir), "BookFile": "user_book1.db", "USI_OwnBook": "true"}
    await factory._rewrite_options_for_remote(instance, options)

    # book は dir 丸ごとではなく FILE 単体で転送される。
    assert len(support.file_transfers) == 1
    local_file, remote_file = support.file_transfers[0]
    assert local_file == book_dir / "user_book1.db"
    assert support.dir_transfers == []

    # composite 表現を維持: BookFile=content-hash 名, BookDir=remote dir。
    assert options["BookFile"] == Path(remote_file).name
    assert options["BookDir"] == str(Path(remote_file).parent)
    assert "user_book1-01234567.db" == Path(remote_file).name


@pytest.mark.asyncio
async def test_large_book_hard_blocks_by_default(tmp_path: Path, monkeypatch) -> None:
    support = _FakeSupport()
    factory = _make_factory(support)
    instance = _ssh_instance(tmp_path)

    book_dir = tmp_path / "books"
    book_dir.mkdir()
    (book_dir / "big.db").write_bytes(b"#YANEURAOU-DB2016 1.00\n")
    monkeypatch.setenv("SHOGIARENA_REMOTE_BOOK_MAX_MB", "0")  # 何でも閾値超扱い
    monkeypatch.delenv("SHOGIARENA_REMOTE_BOOK_TRANSFER", raising=False)

    options = {"BookDir": str(book_dir), "BookFile": "big.db", "USI_OwnBook": "true"}
    with pytest.raises(ValueError, match="too large for automatic remote transfer"):
        await factory._rewrite_options_for_remote(instance, options)
    assert support.file_transfers == []


@pytest.mark.asyncio
async def test_large_book_transfers_with_always_opt_in(tmp_path: Path, monkeypatch) -> None:
    support = _FakeSupport()
    factory = _make_factory(support)
    instance = _ssh_instance(tmp_path)

    book_dir = tmp_path / "books"
    book_dir.mkdir()
    (book_dir / "big.db").write_bytes(b"#YANEURAOU-DB2016 1.00\n")
    monkeypatch.setenv("SHOGIARENA_REMOTE_BOOK_MAX_MB", "0")
    monkeypatch.setenv("SHOGIARENA_REMOTE_BOOK_TRANSFER", "always")

    options = {"BookDir": str(book_dir), "BookFile": "big.db", "USI_OwnBook": "true"}
    await factory._rewrite_options_for_remote(instance, options)
    assert len(support.file_transfers) == 1


@pytest.mark.asyncio
async def test_preplaced_mode_skips_transfer(tmp_path: Path, monkeypatch) -> None:
    support = _FakeSupport()
    factory = _make_factory(support)
    instance = _ssh_instance(tmp_path)

    book_dir = tmp_path / "books"
    book_dir.mkdir()
    (book_dir / "user_book1.db").write_bytes(b"#YANEURAOU-DB2016 1.00\n")
    monkeypatch.setenv("SHOGIARENA_REMOTE_BOOK_TRANSFER", "preplaced")

    options = {"BookDir": str(book_dir), "BookFile": "user_book1.db", "USI_OwnBook": "true"}
    await factory._rewrite_options_for_remote(instance, options)

    # 転送せず、worker 側の同一パスを参照する（composite 維持）。
    assert support.file_transfers == []
    assert support.dir_transfers == []
    assert options["BookDir"] == str(book_dir)
    assert options["BookFile"] == "user_book1.db"


@pytest.mark.asyncio
async def test_disabled_book_falls_back_to_dir_transfer(tmp_path: Path) -> None:
    support = _FakeSupport()
    factory = _make_factory(support)
    instance = _ssh_instance(tmp_path)

    book_dir = tmp_path / "books"
    book_dir.mkdir()
    (book_dir / "user_book1.db").write_bytes(b"x")

    # no_book のときは book 特別扱いせず、BookDir は従来どおり dir 転送される。
    options = {"BookDir": str(book_dir), "BookFile": "no_book"}
    await factory._rewrite_options_for_remote(instance, options)

    assert support.file_transfers == []
    assert len(support.dir_transfers) == 1
    assert support.dir_transfers[0][0] == book_dir.resolve()
