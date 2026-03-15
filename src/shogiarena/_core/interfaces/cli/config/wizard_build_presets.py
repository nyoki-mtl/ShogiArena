"""Build config and overlay preset generation for config wizard."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import yaml

from shogiarena._core.platform.settings.platform_paths import default_output_dir_for_init

ConfirmOverwriteFn = Callable[[str], bool]


def ensure_deeplearningshogi_overlay() -> Path:
    overlay_path = default_output_dir_for_init().parent / "overlays" / "DeepLearningShogi.yaml"
    if overlay_path.exists():
        return overlay_path
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "options": {},
        "engine": {
            "isready_lock_template": "{DNN_Model}.{DNN_Batch_Size}.serialized",
            "isready_lock_check_templates": [
                "{DNN_Model}.*.{DNN_Batch_Size}*.serialized",
                "{DNN_Model2}.*.{DNN_Batch_Size2}*.serialized",
                "{DNN_Model3}.*.{DNN_Batch_Size3}*.serialized",
                "{DNN_Model4}.*.{DNN_Batch_Size4}*.serialized",
                "{DNN_Model5}.*.{DNN_Batch_Size5}*.serialized",
                "{DNN_Model6}.*.{DNN_Batch_Size6}*.serialized",
                "{DNN_Model7}.*.{DNN_Batch_Size7}*.serialized",
                "{DNN_Model8}.*.{DNN_Batch_Size8}*.serialized",
            ],
            "isready_lock_skip_if_exists": True,
        },
    }
    overlay_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return overlay_path


def ensure_yaneuraou_overlay() -> Path:
    overlay_path = default_output_dir_for_init().parent / "overlays" / "YaneuraOu.yaml"
    if overlay_path.exists():
        return overlay_path
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "options": {
            "BookFile": "no_book",
            "NetworkDelay": 0,
            "NetworkDelay2": 0,
        }
    }
    overlay_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return overlay_path


def ensure_fukauraou_overlay() -> Path:
    overlay_path = default_output_dir_for_init().parent / "overlays" / "FukauraOu.yaml"
    if overlay_path.exists():
        return overlay_path
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "options": {
            "BookFile": "no_book",
            "NetworkDelay": 0,
            "NetworkDelay2": 0,
        },
        "engine": {
            "isready_lock_template": "{EvalDir}/{DNN_Model}.{DNN_Batch_Size}.tensorrt",
            "isready_lock_check_templates": [
                "{EvalDir}/{DNN_Model}.*.{DNN_Batch_Size}.TRT*.serialized",
            ],
            "isready_lock_skip_if_exists": True,
        },
    }
    overlay_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return overlay_path


def ensure_build_config(
    path: Path,
    *,
    repo_name: str,
    cuda_path: Path | None = None,
    confirm_overwrite_fn: ConfirmOverwriteFn,
) -> None:
    if not path.exists():
        if repo_name == "YaneuraOu":
            _write_default_yaneuraou_build_config(path)
        elif repo_name == "FukauraOu":
            _write_default_fukauraou_build_config(path, cuda_path=cuda_path)
        elif repo_name == "DeepLearningShogi":
            _write_default_deeplearningshogi_build_config(path)
        else:
            _write_default_build_config(path, repo_name=repo_name)
        print(f"Wrote default build_config to {path}")
        return

    ok, msg = _validate_build_config(path)
    if ok:
        return

    print(f"build_config is invalid: {msg}")
    if not confirm_overwrite_fn("Overwrite with default build_config?"):
        raise SystemExit("Invalid build_config; aborting.")

    if repo_name == "YaneuraOu":
        _write_default_yaneuraou_build_config(path)
    elif repo_name == "FukauraOu":
        _write_default_fukauraou_build_config(path, cuda_path=cuda_path)
    elif repo_name == "DeepLearningShogi":
        _write_default_deeplearningshogi_build_config(path)
    else:
        _write_default_build_config(path, repo_name=repo_name)
    print(f"Rewrote build_config at {path}")


def _write_default_yaneuraou_build_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_windows = sys.platform.startswith("win")
    artifacts = (
        [
            {
                "path": "{work_dir}/YaneuraOu-by-gcc.exe",
                "chmod": "755",
            }
        ]
        if is_windows
        else [
            {
                "path": "{work_dir}/YaneuraOu-by-gcc",
                "chmod": "755",
            }
        ]
    )
    payload = {
        "work_dir": "{repo.path}/source",
        "defaults": {
            "compiler": "clang++",
            "target": "tournament",
            "jobs": 4,
        },
        "commands": [
            ["make", "clean"],
            [
                "make",
                "-j{opts.jobs}",
                "{opts.target}",
                "TARGET_CPU={opts.target_cpu}",
                "COMPILER={opts.compiler}",
                "YANEURAOU_EDITION={opts.edition}",
            ],
        ],
        "artifacts": artifacts,
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _write_default_fukauraou_build_config(path: Path, *, cuda_path: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_windows = sys.platform.startswith("win")
    artifacts = (
        [
            {
                "path": "{work_dir}/YaneuraOu-by-gcc.exe",
                "chmod": "755",
            }
        ]
        if is_windows
        else [
            {
                "path": "{work_dir}/YaneuraOu-by-gcc",
                "chmod": "755",
            }
        ]
    )
    cuda_root = cuda_path or Path("/usr/local/cuda")
    payload = {
        "work_dir": "{repo.path}/source",
        "env": {
            "CUDA_HOME": str(cuda_root),
            "PATH": f"{cuda_root}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        },
        "defaults": {
            "compiler": "clang++",
            "target": "tournament",
            "jobs": 4,
            "edition": "YANEURAOU_ENGINE_DEEP_TENSOR_RT_UBUNTU",
        },
        "commands": [
            ["make", "clean"],
            [
                "make",
                "-j{opts.jobs}",
                "{opts.target}",
                "TARGET_CPU={opts.target_cpu}",
                "COMPILER={opts.compiler}",
                "YANEURAOU_EDITION={opts.edition}",
                f"EXTRA_CPPFLAGS=-I{cuda_root}/include",
                f"EXTRA_LDFLAGS=-L{cuda_root}/lib64",
                f"EXTRA_LDFLAGS+=-L{cuda_root}/lib64/stubs",
            ],
        ],
        "artifacts": artifacts,
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _write_default_deeplearningshogi_build_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_windows = sys.platform.startswith("win")
    artifacts = (
        [
            {
                "path": "{work_dir}/bin/usi.exe",
                "chmod": "755",
            }
        ]
        if is_windows
        else [
            {
                "path": "{work_dir}/bin/usi",
                "chmod": "755",
            }
        ]
    )
    payload = {
        "work_dir": "{repo.path}/usi",
        "env": {
            "CUDA_HOME": "/usr/local/cuda",
            "PATH": "/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        },
        "defaults": {
            "jobs": 4,
        },
        "commands": [
            ["make", "clean"],
            ["make", "-j{opts.jobs}"],
        ],
        "artifacts": artifacts,
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _write_default_build_config(path: Path, *, repo_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "work_dir": "{repo.path}",
        "source_dir": "{repo.path}",
        "env": {
            "TARGET_CPU": "{opts.target_cpu}",
            "EDITION": "{opts.edition}",
        },
        "commands": [
            ["make", "clean"],
            ["make", "all"],
        ],
        "artifacts": [
            {
                "path": "{source_dir}/" + repo_name,
                "chmod": "755",
            }
        ],
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _validate_build_config(path: Path) -> tuple[bool, str]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        return False, f"failed to read: {exc}"
    if not isinstance(data, dict):
        return False, "top-level is not a mapping"
    commands = data.get("commands")
    if not isinstance(commands, list):
        return False, "commands must be a list"
    for cmd in commands:
        if not isinstance(cmd, list) or not all(isinstance(arg, str | int | float) for arg in cmd):
            return False, "commands entries must be lists of scalars"
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return False, "artifacts must be a non-empty list"
    for item in artifacts:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not item.get("path"):
            return False, "artifacts entries must include a non-empty path"
    return True, ""


__all__ = [
    "ensure_build_config",
    "ensure_deeplearningshogi_overlay",
    "ensure_fukauraou_overlay",
    "ensure_yaneuraou_overlay",
]
