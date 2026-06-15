"""path resource（scalar / composite）解決の単体テスト。Task 0013。"""

from __future__ import annotations

from pathlib import Path

from shogiarena._core.shared.kernel.path_resources import (
    BOOK_DISABLED_VALUES,
    CompositeResourceSpec,
    PathResource,
    combine_path,
    is_absolute_path_like,
    resolve_composite_path,
    resolve_path_resources,
)

BOOK_SPEC = CompositeResourceSpec(file_key="BookFile", dir_key="BookDir", default_dir="book")


def _resource_by_keys(resources: list[PathResource], keys: tuple[str, ...]) -> PathResource:
    for resource in resources:
        if resource.option_keys == keys:
            return resource
    raise AssertionError(f"resource {keys} not found in {[r.option_keys for r in resources]}")


class TestIsAbsolutePathLike:
    def test_posix_root_is_absolute(self) -> None:
        assert is_absolute_path_like("/path/to/book.db")

    def test_home_is_absolute(self) -> None:
        assert is_absolute_path_like("~/book.db")

    def test_windows_backslash_root_is_absolute(self) -> None:
        assert is_absolute_path_like("\\book.db")

    def test_drive_letter_is_absolute(self) -> None:
        assert is_absolute_path_like("C:/book.db")

    def test_relative_is_not_absolute(self) -> None:
        assert not is_absolute_path_like("user_book1.db")

    def test_empty_is_not_absolute(self) -> None:
        assert not is_absolute_path_like("")


class TestCombinePath:
    def test_relative_file_joined_with_separator(self) -> None:
        assert combine_path("book", "user_book1.db") == "book/user_book1.db"

    def test_folder_with_trailing_slash_not_doubled(self) -> None:
        assert combine_path("book/", "user_book1.db") == "book/user_book1.db"

    def test_absolute_file_returned_as_is(self) -> None:
        assert combine_path("book", "/abs/user_book1.db") == "/abs/user_book1.db"

    def test_matches_yaneuraou_examples(self) -> None:
        # misc.cpp:2049-2055 のテストケースに対応。
        assert combine_path("xxxx", "/dir") == "/dir"
        assert combine_path("xxxx", "~dir") == "~dir"
        assert combine_path("xxxx", "c:\\dir") == "c:\\dir"
        assert combine_path("xxxx", "yyy") == "xxxx/yyy"
        assert combine_path("xxxx/", "yyy") == "xxxx/yyy"


class TestResolveCompositePath:
    def test_relative_file_combined_with_dir(self) -> None:
        resolved = resolve_composite_path(BOOK_SPEC, file_value="user_book1.db", dir_value="/srv/book")
        assert resolved == "/srv/book/user_book1.db"

    def test_absolute_file_ignores_dir(self) -> None:
        resolved = resolve_composite_path(BOOK_SPEC, file_value="/abs/user_book1.db", dir_value="/srv/book")
        assert resolved == "/abs/user_book1.db"

    def test_missing_dir_uses_engine_default(self) -> None:
        resolved = resolve_composite_path(BOOK_SPEC, file_value="user_book1.db", dir_value=None)
        assert resolved == "book/user_book1.db"

    def test_dir_placeholder_expansion(self) -> None:
        resolved = resolve_composite_path(
            BOOK_SPEC,
            file_value="user_book1.db",
            dir_value="{engine_dir}/book",
            engine_dir=Path("/opt/engines"),
        )
        assert resolved == "/opt/engines/book/user_book1.db"

    def test_file_placeholder_yielding_absolute(self) -> None:
        resolved = resolve_composite_path(
            BOOK_SPEC,
            file_value="{engine_dir}/book/user_book1.db",
            dir_value="/ignored",
            engine_dir=Path("/opt/engines"),
        )
        assert resolved == "/opt/engines/book/user_book1.db"


class TestResolvePathResources:
    def test_book_dir_and_file_form_one_composite(self) -> None:
        resources = resolve_path_resources({"BookDir": "/srv/book", "BookFile": "user_book1.db"})
        composite = _resource_by_keys(resources, ("BookDir", "BookFile"))
        assert composite.kind == "composite"
        assert composite.resolved_path == "/srv/book/user_book1.db"
        assert composite.original_values == {"BookDir": "/srv/book", "BookFile": "user_book1.db"}
        # BookDir は composite に取り込まれ scalar として重複出力されない。
        assert not any(r.kind == "scalar" and r.option_keys == ("BookDir",) for r in resources)

    def test_book_dir_alone_is_scalar(self) -> None:
        resources = resolve_path_resources({"BookDir": "/srv/book"})
        scalar = _resource_by_keys(resources, ("BookDir",))
        assert scalar.kind == "scalar"
        assert scalar.resolved_path == "/srv/book"

    def test_book_file_alone_uses_default_dir(self) -> None:
        resources = resolve_path_resources({"BookFile": "user_book1.db"})
        composite = _resource_by_keys(resources, ("BookFile",))
        assert composite.kind == "composite"
        assert composite.resolved_path == "book/user_book1.db"

    def test_eval_dir_and_file_composite(self) -> None:
        resources = resolve_path_resources({"EvalDir": "/srv/eval", "EvalFile": "nn.bin"})
        composite = _resource_by_keys(resources, ("EvalDir", "EvalFile"))
        assert composite.resolved_path == "/srv/eval/nn.bin"

    def test_custom_scalar_path_option(self) -> None:
        resources = resolve_path_resources(
            {"DNN_Model": "/srv/model.onnx", "MyPath": "/srv/extra"},
            extra_scalar_keys=("MyPath",),
        )
        assert _resource_by_keys(resources, ("DNN_Model",)).resolved_path == "/srv/model.onnx"
        assert _resource_by_keys(resources, ("MyPath",)).resolved_path == "/srv/extra"

    def test_non_string_and_blank_values_ignored(self) -> None:
        resources = resolve_path_resources({"BookDir": "   ", "Threads": 4})
        assert resources == []

    def test_no_book_still_resolved_mechanically(self) -> None:
        # 解決は機械的に行い、no_book 除外は consumer の責務（BOOK_DISABLED_VALUES 参照）。
        resources = resolve_path_resources({"BookFile": "no_book"})
        composite = _resource_by_keys(resources, ("BookFile",))
        assert composite.original_values["BookFile"] in BOOK_DISABLED_VALUES
