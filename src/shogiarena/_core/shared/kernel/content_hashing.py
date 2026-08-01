"""Content-addressed artifact用のfile/tree SHA-256。"""

from __future__ import annotations

import hashlib
import stat
from functools import lru_cache
from pathlib import Path

CANONICAL_ARTIFACT_DIRECTORY_MODE = 0o755
CANONICAL_ARTIFACT_FILE_MODE = 0o644


def sha256_file(path: Path) -> str:
    """File bytesのSHA-256を返す。"""

    if path.is_symlink():
        raise ValueError(f"Artifact file must not be a symlink: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"Artifact file not found: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(path: Path) -> str:
    """Entry kind、relative POSIX path、mode、contentからtree digestを返す。"""

    if path.is_symlink():
        raise ValueError(f"Artifact tree root must not be a symlink: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"Artifact directory not found: {path}")
    digest = hashlib.sha256()
    children = sorted(path.rglob("*"), key=lambda child: child.relative_to(path).as_posix())
    for child in children:
        relative_path = child.relative_to(path).as_posix()
        mode = child.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError(f"Artifact tree must not contain symlinks: {child}")
        if stat.S_ISDIR(mode):
            kind = b"D"
            payload = b""
            canonical_mode = CANONICAL_ARTIFACT_DIRECTORY_MODE
        elif stat.S_ISREG(mode):
            kind = b"F"
            payload = child.read_bytes()
            canonical_mode = CANONICAL_ARTIFACT_FILE_MODE
        else:
            raise ValueError(f"Artifact tree contains unsupported entry: {child}")
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(kind)
        digest.update(b"\0")
        digest.update(oct(canonical_mode).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def sha256_path(path: Path) -> tuple[str, str]:
    """Path kindとcontent digestを返す。"""

    if path.is_file():
        return "file", sha256_file(path)
    if path.is_dir():
        return "directory", sha256_tree(path)
    raise FileNotFoundError(f"Artifact path not found: {path}")


def sha256_file_cached(path: Path) -> str:
    """Unchanged file contentのdigestをstat identity単位で再利用する。"""

    if path.is_symlink():
        raise ValueError(f"Artifact file must not be a symlink: {path}")
    try:
        metadata = path.stat()
    except FileNotFoundError:
        raise FileNotFoundError(f"Artifact file not found: {path}") from None
    if not stat.S_ISREG(metadata.st_mode):
        raise FileNotFoundError(f"Artifact file not found: {path}")
    resolved = path.resolve()
    return _sha256_file_cached(str(resolved), metadata.st_size, metadata.st_mtime_ns)


@lru_cache(maxsize=512)
def _sha256_file_cached(path: str, size: int, mtime_ns: int) -> str:
    del size, mtime_ns
    return sha256_file(Path(path))


def sha256_path_cached(path: Path) -> tuple[str, str]:
    """Unchanged file/tree contentのdigestをstat identity単位で再利用する。"""

    if path.is_symlink():
        raise ValueError(f"Artifact path must not be a symlink: {path}")
    if path.is_file():
        return "file", sha256_file_cached(path)
    if path.is_dir():
        resolved = path.resolve()
        signature = _tree_metadata_signature(resolved)
        return "directory", _sha256_tree_cached(str(resolved), signature)
    raise FileNotFoundError(f"Artifact path not found: {path}")


def _tree_metadata_signature(path: Path) -> tuple[tuple[str, str, int, int], ...]:
    entries: list[tuple[str, str, int, int]] = []
    children = sorted(path.rglob("*"), key=lambda child: child.relative_to(path).as_posix())
    for child in children:
        relative_path = child.relative_to(path).as_posix()
        metadata = child.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"Artifact tree must not contain symlinks: {child}")
        if stat.S_ISDIR(metadata.st_mode):
            kind = "directory"
        elif stat.S_ISREG(metadata.st_mode):
            kind = "file"
        else:
            raise ValueError(f"Artifact tree contains unsupported entry: {child}")
        entries.append((relative_path, kind, metadata.st_size, metadata.st_mtime_ns))
    return tuple(entries)


@lru_cache(maxsize=128)
def _sha256_tree_cached(path: str, signature: tuple[tuple[str, str, int, int], ...]) -> str:
    del signature
    return sha256_tree(Path(path))


__all__ = [
    "CANONICAL_ARTIFACT_DIRECTORY_MODE",
    "CANONICAL_ARTIFACT_FILE_MODE",
    "sha256_file",
    "sha256_file_cached",
    "sha256_path",
    "sha256_path_cached",
    "sha256_tree",
]
