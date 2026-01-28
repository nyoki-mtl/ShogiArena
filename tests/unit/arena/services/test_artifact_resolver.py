from __future__ import annotations

import io
import os
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from shogiarena.arena.services.artifacts import resolver as resolver_module
from shogiarena.arena.services.artifacts.resolver import ArtifactResolver
from shogiarena.utils.common.settings import RepoSettings


def _write_build_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "work_dir": "{repo.path}",
        "source_dir": "{repo.path}/source",
        "git": {"checkout": "{opts.commit}", "clean": "{opts.git_clean}"},
        "commands": [],
        "artifacts": [
            {
                "path": "{source_dir}/YaneuraOu_test",
                "chmod": "755",
                "requires_elf": True,
            }
        ],
    }
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def _set_repo(monkeypatch, repo_root: Path, build_config: Path) -> None:
    monkeypatch.setattr(
        resolver_module.project_dirs,
        "repos",
        {
            "YaneuraOu": RepoSettings(
                name="YaneuraOu",
                path=repo_root,
                url=None,
                build_config=build_config,
            )
        },
    )


def test_resolver_serializes_builds(tmp_path, monkeypatch) -> None:
    engine_root = tmp_path / "engines"
    engine_root.mkdir()
    monkeypatch.setattr(resolver_module.project_dirs, "engine_dir", engine_root)
    build_config = tmp_path / "builds" / "yaneuraou.yaml"
    _write_build_config(build_config)
    _set_repo(monkeypatch, tmp_path / "repos" / "YaneuraOu", build_config)

    built_paths: list[os.PathLike[str]] = []

    def fake_build(self, engine_root, art, build_opts, *, repo, artifact_id, cfg):
        out = engine_root / "mock-binary"
        out.write_bytes(b"\x7fELF" + b"\x00" * 8)
        os.chmod(out, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        staged = self._stage_artifact(engine_root, art, repo, artifact_id, build_opts, out, [])
        built_paths.append(staged)
        return staged

    monkeypatch.setattr(ArtifactResolver, "_build_from_yaml", fake_build, raising=False)

    resolver = ArtifactResolver()
    artifact = "YaneuraOu/a5ee2786"
    overrides = {"target_cpu": "ZEN3", "edition": "YANEURAOU_ENGINE_NNUE_HALFKP_512X2_8_64"}

    def _resolve() -> str:
        return str(resolver.resolve(artifact, overrides=overrides))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_resolve) for _ in range(2)]
        results = [f.result(timeout=5) for f in futures]

    assert len(built_paths) == 1
    assert results[0] == results[1]


def test_git_env_includes_safe_directory(monkeypatch, tmp_path) -> None:
    path = tmp_path / "repo"
    path.mkdir()
    env = ArtifactResolver._git_env_with_safe_directory(path)
    assert env.get("GIT_CONFIG_COUNT") == "1"
    assert env.get("GIT_CONFIG_KEY_0") == "safe.directory"
    assert env.get("GIT_CONFIG_VALUE_0") == str(path.resolve())

    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "safe.directory")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(path.resolve()))
    env2 = ArtifactResolver._git_env_with_safe_directory(path)
    # Count stays the same because entry already present
    assert env2.get("GIT_CONFIG_COUNT") == "1"


def _setup_repo_with_binary(tmp_path, monkeypatch):
    monkeypatch.delenv("SHOGIARENA_STREAM_BUILD_LOGS", raising=False)
    repo_root = tmp_path / "repos" / "YaneuraOu"
    src_dir = repo_root / "source"
    src_dir.mkdir(parents=True)
    (src_dir / "Makefile").write_text("all:\n\t@echo ok\n", encoding="utf-8")
    binary = src_dir / "YaneuraOu_test"
    binary.write_bytes(b"\x7fELFxxxx")
    os.chmod(binary, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    engine_root = tmp_path / "engines"
    monkeypatch.setattr(resolver_module.project_dirs, "engine_dir", engine_root)
    build_config = tmp_path / "builds" / "yaneuraou.yaml"
    _write_build_config(build_config)
    _set_repo(monkeypatch, repo_root, build_config)

    return repo_root, src_dir, binary


def test_dirty_worktree_is_stashed_and_restored(monkeypatch, tmp_path) -> None:
    _repo_root, _src_dir, _ = _setup_repo_with_binary(tmp_path, monkeypatch)

    git_calls: list[list[str]] = []

    def fake_check_output(cmd, cwd=None, env=None):
        if cmd[:2] == ["git", "status"]:
            return b"M dummy\n"
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return b"main\n"
        if cmd[:2] == ["git", "rev-parse"]:
            return b"a5ee2786\n"
        raise AssertionError(f"unexpected check_output: {cmd}")

    def fake_check_call(cmd, cwd=None, stdout=None, stderr=None, env=None):
        git_calls.append(cmd)
        return 0

    monkeypatch.setattr("subprocess.check_output", fake_check_output)
    monkeypatch.setattr("subprocess.check_call", fake_check_call)

    resolver = ArtifactResolver()
    overrides = {"target_cpu": "ZEN3", "edition": "YANEURAOU_ENGINE_NNUE_HALFKP_512X2_8_64"}
    path = resolver.resolve("YaneuraOu/a5ee2786", overrides=overrides)
    assert Path(path).exists()
    assert any(cmd[:2] == ["git", "stash"] for cmd in git_calls)
    assert any(cmd[:2] == ["git", "checkout"] and cmd[-1] == "main" for cmd in git_calls)
    assert any(cmd[:2] == ["git", "stash"] and cmd[-1] == "pop" for cmd in git_calls)


def test_resolver_uses_shared_lock_per_commit(tmp_path, monkeypatch) -> None:
    engine_root = tmp_path / "engines"
    engine_root.mkdir()
    monkeypatch.setattr(resolver_module.project_dirs, "engine_dir", engine_root)
    build_config = tmp_path / "builds" / "yaneuraou.yaml"
    _write_build_config(build_config)
    _set_repo(monkeypatch, tmp_path / "repos" / "YaneuraOu", build_config)

    lock_paths: list[Path] = []

    class DummyLock:
        def __init__(self, path: Path, *, timeout: float, poll_interval: float) -> None:
            lock_paths.append(Path(path))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(resolver_module, "FileLock", DummyLock)

    def fake_build(self, engine_root, art, build_opts, *, repo, artifact_id, cfg):
        tag = build_opts.get("tune_tag", "vanilla")
        out = engine_root / f"mock-{tag}"
        out.write_bytes(b"\x7fELF" + os.urandom(8))
        os.chmod(out, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        return self._stage_artifact(engine_root, art, repo, artifact_id, build_opts, out, [])

    monkeypatch.setattr(ArtifactResolver, "_build_from_yaml", fake_build, raising=False)

    resolver = ArtifactResolver()
    artifact = "YaneuraOu/a5ee2786"
    base = {"target_cpu": "ZEN3", "edition": "YANEURAOU_ENGINE_NNUE_HALFKP_512X2_8_64"}

    resolver.resolve(artifact, overrides={**base, "tune_tag": "alpha"})
    resolver.resolve(artifact, overrides={**base, "tune_tag": "beta"})

    assert len(lock_paths) == 1
    assert lock_paths[0].name == "YaneuraOu_a5ee2786.lock"


def test_stream_build_logs_env(monkeypatch) -> None:
    monkeypatch.delenv("SHOGIARENA_STREAM_BUILD_LOGS", raising=False)
    assert ArtifactResolver._stream_build_logs_enabled() is False
    monkeypatch.setenv("SHOGIARENA_STREAM_BUILD_LOGS", "1")
    assert ArtifactResolver._stream_build_logs_enabled() is True


def test_run_logged_subprocess_streams_output(monkeypatch, tmp_path) -> None:
    log_buffer = io.StringIO()

    class DummyStdout(io.StringIO):
        def flush(self) -> None:
            pass

    dummy_stdout = DummyStdout()
    monkeypatch.setattr(resolver_module.sys, "stdout", dummy_stdout)

    ArtifactResolver._run_logged_subprocess(
        [sys.executable, "-c", "print('hello')"],
        cwd=tmp_path,
        log_file=log_buffer,
        env=None,
        stream=True,
    )

    assert "hello" in log_buffer.getvalue()
    assert "hello" in dummy_stdout.getvalue()
