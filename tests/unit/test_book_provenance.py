"""book provenance / fingerprint の単体テスト。Task 0015。"""

from __future__ import annotations

from pathlib import Path

from shogiarena._core.shared.kernel.book_provenance import (
    build_book_provenance,
    fingerprint_file,
)


class TestFingerprintFile:
    def test_lightweight_default(self, tmp_path: Path) -> None:
        book = tmp_path / "book.db"
        book.write_bytes(b"a" * 1024)
        fp = fingerprint_file(book)
        assert fp["basename"] == "book.db"
        assert fp["size"] == 1024
        assert fp["hash_method"] == "size_partial_sha256"
        assert "partial_sha256" in fp
        assert "full_sha256" not in fp

    def test_full_hash_opt_in(self, tmp_path: Path) -> None:
        book = tmp_path / "book.db"
        book.write_bytes(b"abc" * 10)
        fp = fingerprint_file(book, full_hash=True)
        assert fp["hash_method"] == "full_sha256"
        assert "full_sha256" in fp
        assert "partial_sha256" not in fp

    def test_partial_hash_changes_with_content(self, tmp_path: Path) -> None:
        a = tmp_path / "a.db"
        b = tmp_path / "b.db"
        a.write_bytes(b"x" * 2048)
        b.write_bytes(b"y" * 2048)
        assert fingerprint_file(a)["partial_sha256"] != fingerprint_file(b)["partial_sha256"]

    def test_missing_file(self, tmp_path: Path) -> None:
        fp = fingerprint_file(tmp_path / "absent.db")
        assert fp["hash_method"] == "missing"
        assert "size" not in fp


class TestBuildBookProvenance:
    def test_disabled_returns_none(self) -> None:
        assert build_book_provenance({"BookFile": "no_book"}) is None

    def test_records_resolved_path_options_and_fingerprint(self, tmp_path: Path) -> None:
        book = tmp_path / "user_book1.db"
        book.write_bytes(b"#YANEURAOU-DB2016 1.00\n")
        provenance = build_book_provenance(
            {
                "BookDir": str(tmp_path),
                "BookFile": "user_book1.db",
                "BookOnTheFly": "true",
                "BookMoves": 16,
                "Threads": 4,
            }
        )
        assert provenance is not None
        assert provenance["resolved_path"] == str(book)
        fingerprint = provenance["fingerprint"]
        assert isinstance(fingerprint, dict)
        assert fingerprint["basename"] == "user_book1.db"
        options = provenance["options"]
        assert isinstance(options, dict)
        # 主要 book option のみ記録し、無関係な Threads は含めない。
        assert options["BookOnTheFly"] == "true"
        assert "BookMoves" in options
        assert "Threads" not in options

    def test_relative_path_has_no_fingerprint(self) -> None:
        provenance = build_book_provenance({"BookDir": "book", "BookFile": "user_book1.db"})
        assert provenance is not None
        assert provenance["resolved_path"] == "book/user_book1.db"
        assert "fingerprint" not in provenance
