"""`operation()` の入れ子ガードは、それが守る session と同じスコープでなければならない。

session は `scoped_session` により thread / asyncio task ごとに分かれる。ガードだけを
repository 単位の bool で持つと、dashboard の REST handler が `asyncio.to_thread` で
並行に走ったときに、無関係なスレッド同士が互いを「入れ子」と誤検出して 500 になる。
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from shogiarena._core.platform.db.store.repository_factory import SQLiteShogiDBFactory


def _make_repository(tmp_path: Path):  # noqa: ANN202 - ShogiRepository は内部型
    repository = SQLiteShogiDBFactory(tmp_path / "game.db").create()
    repository.create_tables()
    return repository


def test_nested_operation_on_the_same_session_still_fails(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path)
    try:
        with repository.operation():
            with pytest.raises(RuntimeError, match="cannot be nested"):
                with repository.operation():
                    pass
    finally:
        repository.close_db()


def test_concurrent_operations_from_separate_threads_do_not_collide(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path)
    entered = threading.Barrier(2, timeout=10.0)
    errors: list[BaseException] = []

    def hold_an_operation() -> None:
        try:
            with repository.operation():
                # Both threads sit inside their own boundary at the same time, which is exactly
                # what the dashboard does once summary and update-detail run off the event loop.
                entered.wait()
        except BaseException as error:  # noqa: BLE001 - 失敗を主スレッドへ運ぶ
            errors.append(error)

    threads = [threading.Thread(target=hold_an_operation) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10.0)
    finally:
        repository.close_db()

    assert errors == []


def test_operation_guard_is_released_after_an_exception(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path)
    try:
        with pytest.raises(ValueError, match="boom"):
            with repository.operation():
                raise ValueError("boom")

        # A failed boundary must not leave the session marked as open.
        with repository.operation() as session:
            assert session is not None
    finally:
        repository.close_db()
