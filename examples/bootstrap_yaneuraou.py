"""公式YaneuraOuと水匠5を使うtournament／SPSA quick-startを準備する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

YANEURAOU_TAG = "V9.00"
YANEURAOU_COMMIT = "a5ee2786c0030edc7d4a1cdfe94b04dffec55493"
YANEURAOU_REPOSITORY = "https://github.com/yaneurao/YaneuraOu.git"
SHOGIARENA_SPSA_COMMIT = "6b014a16ea0648ab04c62dd245a40a46306a0835"
SHOGIARENA_SPSA_REPOSITORY = "https://github.com/nyoki-mtl/YaneuraOu.git"
SUISHO5_URL = "https://github.com/yaneurao/YaneuraOu/releases/download/suisho5/Suisho5.7z"
SUISHO5_SHA256 = "6734e3a3d28e67b9206c3442f6d10f16148138327dff811cadedfcf581f79809"
BOOTSTRAP_SCHEMA_VERSION = 1
OPENINGS_SOURCE = Path(__file__).resolve().parent / "configs" / "resources" / "openings" / "pair_positions.sfen"


@dataclass(frozen=True)
class PrebuiltPlan:
    """公式prebuiltから選ぶplatform別binary。"""

    archive_name: str
    archive_url: str
    archive_sha256: str
    member: str


WINDOWS_X64_PLAN = PrebuiltPlan(
    archive_name="yaneuraou-V900-git-win64-all.7z",
    archive_url=("https://github.com/yaneurao/YaneuraOu/releases/download/V9.00/yaneuraou-V900-git-win64-all.7z"),
    archive_sha256="6517997dd05ba049a2244a828216967a0ad351d975ec52a0f358e2883197dec6",
    member=("NNUE_halfkp_256x2_32_32/YaneuraOu_NNUE_halfkp_256x2_32_32-V900Git_SSE41.exe"),
)
MACOS_ARM64_PLAN = PrebuiltPlan(
    archive_name="yaneuraou-V900-git-mac-all.7z",
    archive_url=("https://github.com/yaneurao/YaneuraOu/releases/download/V9.00/yaneuraou-V900-git-mac-all.7z"),
    archive_sha256="3aad1e03386ab6e311fd592da20974f0a032c169177783ab97c2ec745df33418",
    member=("NNUE_halfkp_256x2_32_32/YaneuraOu_NNUE_halfkp_256x2_32_32-V900Git_APPLEM1"),
)
MACOS_X64_PLAN = PrebuiltPlan(
    archive_name=MACOS_ARM64_PLAN.archive_name,
    archive_url=MACOS_ARM64_PLAN.archive_url,
    archive_sha256=MACOS_ARM64_PLAN.archive_sha256,
    member=("NNUE_halfkp_256x2_32_32/YaneuraOu_NNUE_halfkp_256x2_32_32-V900Git_APPLESSE42"),
)


def _normalized_machine(machine: str) -> str:
    return machine.strip().lower().replace("amd64", "x86_64").replace("aarch64", "arm64")


def select_prebuilt_plan(system: str, machine: str) -> PrebuiltPlan | None:
    """Platformに対応する公式prebuiltを返す。Linuxはsource buildなのでNone。"""

    normalized_system = system.strip().lower()
    normalized_machine = _normalized_machine(machine)
    if normalized_system == "windows" and normalized_machine == "x86_64":
        return WINDOWS_X64_PLAN
    if normalized_system == "darwin" and normalized_machine == "arm64":
        return MACOS_ARM64_PLAN
    if normalized_system == "darwin" and normalized_machine == "x86_64":
        return MACOS_X64_PLAN
    if normalized_system == "linux" and normalized_machine in {"x86_64", "arm64"}:
        return None
    raise RuntimeError(f"unsupported platform: system={system!r}, machine={machine!r}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path, expected_sha256: str) -> None:
    if destination.is_file() and _sha256(destination) == expected_sha256:
        print(f"Using cached download: {destination.name}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "ShogiArena-example-bootstrap/1"})
    print(f"Downloading {url}")
    with urllib.request.urlopen(request) as response, partial.open("wb") as output:  # noqa: S310
        shutil.copyfileobj(response, output)
    actual_sha256 = _sha256(partial)
    if actual_sha256 != expected_sha256:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"download digest mismatch for {destination.name}: expected={expected_sha256}, actual={actual_sha256}"
        )
    partial.replace(destination)


def _extract_member(archive: Path, member: str, destination: Path) -> None:
    try:
        import py7zr
    except ImportError as exc:
        raise RuntimeError(
            "py7zr is required. Run this script with `uv run --with py7zr python examples/bootstrap_yaneuraou.py`."
        ) from exc

    extract_root = destination.parent / f".{destination.name}-extract"
    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True)
    try:
        with py7zr.SevenZipFile(archive, mode="r") as handle:
            handle.extract(path=extract_root, targets=[member])
        extracted = extract_root / Path(member)
        if not extracted.is_file():
            raise RuntimeError(f"archive member not found after extraction: {member}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(extracted, destination)
    finally:
        shutil.rmtree(extract_root, ignore_errors=True)


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    print("+", subprocess.list2cmdline(command))
    subprocess.run(command, cwd=cwd, check=True)


def _prepare_linux_binary(runtime_dir: Path, destination: Path, machine: str) -> None:
    if shutil.which("git") is None or shutil.which("make") is None:
        raise RuntimeError("Linux source build requires git and make in PATH")
    compiler = "clang++" if shutil.which("clang++") else "g++" if shutil.which("g++") else None
    if compiler is None:
        raise RuntimeError("Linux source build requires clang++ or g++ in PATH")

    source_root = runtime_dir / "YaneuraOu"
    if not source_root.exists():
        _run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                YANEURAOU_TAG,
                YANEURAOU_REPOSITORY,
                str(source_root),
            ]
        )
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source_root, text=True).strip()
    if head != YANEURAOU_COMMIT:
        raise RuntimeError(f"unexpected YaneuraOu checkout: expected={YANEURAOU_COMMIT}, actual={head}")

    normalized_machine = _normalized_machine(machine)
    target_cpu = "SSE2" if normalized_machine == "x86_64" else "OTHER"
    source_dir = source_root / "source"
    jobs = str(max(1, min(os.cpu_count() or 2, 8)))
    _run(["make", "clean"], cwd=source_dir)
    _run(
        [
            "make",
            f"-j{jobs}",
            "tournament",
            f"TARGET_CPU={target_cpu}",
            f"COMPILER={compiler}",
            "YANEURAOU_EDITION=YANEURAOU_ENGINE_NNUE_HALFKP_256X2_32_32",
        ],
        cwd=source_dir,
    )
    built = source_dir / "YaneuraOu-by-gcc"
    if not built.is_file():
        raise RuntimeError(f"built YaneuraOu binary not found: {built}")
    shutil.copy2(built, destination)


def _write_configs(runtime_dir: Path, engine_binary: Path) -> tuple[Path, Path, Path, Path]:
    engine_dir = engine_binary.parent
    engine_config = runtime_dir / "engine.yaml"
    spsa_engine_config = runtime_dir / "spsa-engine.yaml"
    tournament_config = runtime_dir / "tournament.yaml"
    spsa_config = runtime_dir / "spsa.yaml"
    spsa_space = runtime_dir / "spsa-space.yaml"
    openings_file = runtime_dir / "openings.sfen"
    quote = json.dumps
    shutil.copy2(OPENINGS_SOURCE, openings_file)
    engine_config.write_text(
        "\n".join(
            [
                'name: "YaneuraOu V9.00 + Suisho5"',
                f"engine_path: {quote(engine_binary.as_posix())}",
                f"working_directory: {quote(engine_dir.as_posix())}",
                "options:",
                "  Threads: 2",
                "  USI_Hash: 128",
                "  BookFile: no_book",
                "  FV_SCALE: 24",
                "  NetworkDelay: 0",
                "  NetworkDelay2: 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    spsa_engine_config.write_text(
        "\n".join(
            [
                'name: "YaneuraOu V9.00 + Suisho5 (SPSA)"',
                f"engine_path: {quote(engine_binary.as_posix())}",
                f"working_directory: {quote(engine_dir.as_posix())}",
                "options:",
                "  Threads: 2",
                "  USI_Hash: 128",
                "  BookFile: no_book",
                "  NetworkDelay: 0",
                "  NetworkDelay2: 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    output_dir = runtime_dir / "output"
    tournament_config.write_text(
        "\n".join(
            [
                'experiment_name: "yaneuraou-suisho5-quickstart"',
                f"output_dir: {quote(output_dir.as_posix())}",
                "engines:",
                '  - name: "YaneuraOu A"',
                '    engine_path: "engine.yaml"',
                '  - name: "YaneuraOu B"',
                '    engine_path: "engine.yaml"',
                "tournament:",
                '  scheduler: "round_robin"',
                "  games_per_pair: 4",
                "  num_parallel: 2",
                "  engine_lifecycle: per_game",
                "rules:",
                "  initial_positions:",
                "    type: file",
                '    source: "openings.sfen"',
                "    source_format: sfen",
                "    flip_policy: pair_both",
                "    sync_scope: pair",
                "  time_control:",
                "    node_limit: 100000",
                "  adjudication:",
                "    enable_max_plies: true",
                "    max_plies: 80",
                "    sync_max_plies_with_engine: true",
                "dashboard:",
                "  enabled: true",
                "  api_port: 8080",
                "logging:",
                "  usi_transcript: true",
                "  usi_transcript_detail: commands",
                "",
            ]
        ),
        encoding="utf-8",
    )
    spsa_space.write_text(
        "\n".join(
            [
                "schema_version: shogiarena.spsa.space.v1",
                "target:",
                "  engine_family: yaneuraou",
                "  protocol: usi_options",
                "  required_options_policy: strict",
                "  tunable_manifest:",
                "    required: false",
                "    command: usi_tunables",
                "parameters:",
                "  - id: fv_scale",
                "    label: FV_SCALE",
                "    target:",
                "      option: FV_SCALE",
                "      value_encoding: integer",
                "    value_type: int",
                "    initial: 24",
                "    bounds:",
                "      min: 16",
                "      max: 32",
                "    schedule:",
                "      c_end: 2",
                "      r_end: 0.002",
                "    rounding:",
                "      mode: stochastic",
                "",
            ]
        ),
        encoding="utf-8",
    )
    spsa_config.write_text(
        "\n".join(
            [
                'experiment_name: "yaneuraou-suisho5-spsa-quickstart"',
                "engines:",
                '  - name: "YaneuraOu FV_SCALE"',
                f"    engine_path: {quote(spsa_engine_config.as_posix())}",
                "dashboard:",
                "  enabled: true",
                "  api_port: 8080",
                "rules:",
                "  initial_positions:",
                "    type: file",
                '    source: "openings.sfen"',
                "    source_format: sfen",
                "    flip_policy: pair_both",
                "    sync_scope: pair",
                "  repetition_occurrences_to_draw: 2",
                "  time_control:",
                "    node_limit: 30000",
                "  adjudication:",
                "    enable_max_plies: true",
                "    max_plies: 80",
                "    sync_max_plies_with_engine: true",
                "spsa:",
                '  space: "spsa-space.yaml"',
                "  num_updates: 2",
                "  pairs_per_update: 1",
                "  inflight_factor: 2",
                "  num_parallel: 2",
                "  algorithm:",
                "    name: classic",
                "    alpha: 0.602",
                "    gamma: 0.101",
                "    A:",
                "      mode: ratio",
                "      value: 0.1",
                "  variants:",
                "    pairing: plus_minus",
                "    crn: true",
                "    integer_rounding: stochastic",
                "    apply:",
                "      clear_hash: false",
                "      after_setoption: isready",
                "system:",
                "  engine_handshake_timeout: 5",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return engine_config, tournament_config, spsa_config, spsa_space


def _write_search_spsa_configs(runtime_dir: Path, engine_binary: Path) -> tuple[Path, Path, Path]:
    """探索parameterを公開したfork向けのSPSA設定を書く。"""

    engine_dir = engine_binary.parent
    engine_config = runtime_dir / "search-spsa-engine.yaml"
    spsa_config = runtime_dir / "search-spsa.yaml"
    spsa_space = runtime_dir / "search-spsa-space.yaml"
    spsa_instances = runtime_dir / "search-spsa-instances.yaml"
    quote = json.dumps
    # 1 対局は engine プロセスを 2 つ使うため、同時 4 対局には 8 engine が要る。
    # この設定が無いとローカルインスタンスは slots: 4 の既定 (= 同時 2 対局) になる。
    spsa_instances.write_text(
        "\n".join(
            [
                "# 同時対局数 = min(max_engines, slots) / 2",
                "# Threads: 2 の engine で同時 4 対局 = 16 スレッド。物理コア数に合わせて調整する。",
                "name: local",
                "type: local",
                "slots: 16",
                "max_engines: 8",
                'tags: ["local"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    engine_config.write_text(
        "\n".join(
            [
                'name: "YaneuraOu V9.40 ShogiArena SPSA + Suisho5"',
                f"engine_path: {quote(engine_binary.as_posix())}",
                f"working_directory: {quote(engine_dir.as_posix())}",
                "options:",
                "  Threads: 2",
                "  USI_Hash: 128",
                "  BookFile: no_book",
                "  NetworkDelay: 0",
                "  NetworkDelay2: 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    spsa_space.write_text(
        "\n".join(
            [
                "schema_version: shogiarena.spsa.space.v1",
                "target:",
                "  engine_family: yaneuraou",
                "  protocol: usi_options",
                "  required_options_policy: strict",
                "  tunable_manifest:",
                "    required: true",
                "    command: usi_tunables",
                "select:",
                "  - aspiration_window_1",
                "  - aspiration_window_2",
                "  - lowPlyHistory_fill_1",
                "  - correction_value_1",
                "  - correction_value_2",
                "  - update_correction_history1_1",
                "  - Search_correction_history_bonus_1",
                "  - YaneuraOuWorker_clear2_1",
                "  - YaneuraOuWorker_clear3_1",
                "  - Search_tt_lookup1_1",
                "  - Search_tt_lookup1_2",
                "  - Search_tt_lookup2_1",
                "  - Search_static_evaluation_1a_1",
                "  - Search_static_evaluation_1a_2",
                "  - Search_razoring_1",
                "  - Search_futility_1_1",
                "  - Search_futility_1_3",
                "  - Search_nullmove_1_1",
                "  - Search_nullmove_1_2",
                "  - Search_Probcut_1",
                "  - Search_Probcut_2",
                "  - Search_small_Probcut_1",
                "  - Search_Continuation_history_based_pruning1_2",
                "  - Search_Extensions1_1",
                "  - Search_Extensions3_2",
                "  - YaneuraOuWorker_reduction_1",
                "  - YaneuraOuWorker_reduction_2",
                "  - Search_LMR_research_thresholds_1",
                "  - Search_Capture_SEE_pruning_margin_1",
                "  - QSearch_SEE_pruning_1",
                "  - MovePicker_good_capture_see_1",
                "  - MovePicker_quiet_partial_sort_1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    spsa_config.write_text(
        "\n".join(
            [
                'experiment_name: "yaneuraou-v940-search-spsa"',
                "engines:",
                '  - name: "YaneuraOu search parameters"',
                f"    engine_path: {quote(engine_config.as_posix())}",
                "instances:",
                f"  - {quote(spsa_instances.as_posix())}",
                "dashboard:",
                "  enabled: true",
                "  api_port: 8080",
                "rules:",
                "  initial_positions:",
                "    type: file",
                '    source: "openings.sfen"',
                "    source_format: sfen",
                "    flip_policy: pair_both",
                "    sync_scope: pair",
                "  repetition_occurrences_to_draw: 2",
                "  time_control:",
                "    node_limit: 30000",
                "  adjudication:",
                "    enable_max_plies: true",
                "    max_plies: 320",
                "    sync_max_plies_with_engine: true",
                "spsa:",
                f"  space: {quote(spsa_space.as_posix())}",
                "  num_updates: 2",
                "  pairs_per_update: 2",
                "  inflight_factor: 2",
                "  num_parallel: 4",
                "  algorithm:",
                "    name: classic",
                "    alpha: 0.602",
                "    gamma: 0.101",
                "    A: {mode: ratio, value: 0.1}",
                "  variants:",
                "    pairing: plus_minus",
                "    crn: true",
                "    integer_rounding: stochastic",
                "    apply:",
                "      clear_hash: true",
                "      after_setoption: isready",
                "  # ltc_regression (チューニング中の LTC 回帰テスト) は設定しない。",
                "  # 実行中に挟める標本サイズでは、検出できるのが破綻に近い劣化だけになる。",
                "  # Stockfish/fishtest も SPSA の実行中には LTC 検証を挟まない。",
                "  # 検証は終了後に run sprt の独立ランで行う。docs の「LTC 回帰テスト」を参照。",
                "system:",
                "  engine_handshake_timeout: 10",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return engine_config, spsa_config, spsa_space


def prepare_search_spsa(runtime_dir: Path, source_binary: Path) -> tuple[Path, Path, Path]:
    """Build済みforkとbootstrap済み水匠5から探索SPSA runtimeを作る。"""

    runtime_dir = runtime_dir.expanduser().resolve()
    source_binary = source_binary.expanduser().resolve()
    eval_source = runtime_dir / "engine" / "eval" / "nn.bin"
    if not source_binary.is_file():
        raise FileNotFoundError(f"ShogiArena SPSA engine binary not found: {source_binary}")
    if not eval_source.is_file():
        raise FileNotFoundError(f"Suisho5 evaluation file not found; run the bootstrap first: {eval_source}")

    engine_dir = runtime_dir / "search-spsa-engine"
    engine_dir.mkdir(parents=True, exist_ok=True)
    destination = engine_dir / ("YaneuraOu.exe" if source_binary.suffix.lower() == ".exe" else "YaneuraOu")
    shutil.copy2(source_binary, destination)
    eval_destination = engine_dir / "eval" / "nn.bin"
    eval_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(eval_source, eval_destination)
    return _write_search_spsa_configs(runtime_dir, destination)


def bootstrap(runtime_dir: Path, *, force: bool) -> tuple[Path, Path, Path, Path]:
    """Platform用engine、評価関数、ShogiArena設定を準備する。"""

    runtime_dir = runtime_dir.expanduser().resolve()
    downloads_dir = runtime_dir / "downloads"
    engine_dir = runtime_dir / "engine"
    marker_path = runtime_dir / "bootstrap.json"
    system = platform.system()
    machine = platform.machine()
    plan = select_prebuilt_plan(system, machine)

    engine_dir.mkdir(parents=True, exist_ok=True)
    engine_binary = engine_dir / ("YaneuraOu.exe" if system == "Windows" else "YaneuraOu")
    eval_file = engine_dir / "eval" / "nn.bin"
    if force:
        engine_binary.unlink(missing_ok=True)
        eval_file.unlink(missing_ok=True)

    if not engine_binary.is_file():
        if plan is None:
            _prepare_linux_binary(runtime_dir, engine_binary, machine)
        else:
            archive = downloads_dir / plan.archive_name
            _download(plan.archive_url, archive, plan.archive_sha256)
            _extract_member(archive, plan.member, engine_binary)
    if system != "Windows":
        engine_binary.chmod(0o755)

    if not eval_file.is_file():
        eval_archive = downloads_dir / "Suisho5.7z"
        _download(SUISHO5_URL, eval_archive, SUISHO5_SHA256)
        _extract_member(eval_archive, "nn.bin", eval_file)

    engine_config, tournament_config, spsa_config, spsa_space = _write_configs(runtime_dir, engine_binary)
    marker_path.write_text(
        json.dumps(
            {
                "schema_version": BOOTSTRAP_SCHEMA_VERSION,
                "platform": {"system": system, "machine": machine},
                "yaneuraou": {"tag": YANEURAOU_TAG, "commit": YANEURAOU_COMMIT},
                "engine_sha256": _sha256(engine_binary),
                "suisho5_sha256": _sha256(eval_file),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return engine_config, tournament_config, spsa_config, spsa_space


def main() -> int:
    """Bootstrapを実行し、次に打つコマンドを表示する。"""

    default_runtime = Path(__file__).resolve().parent / ".runtime" / "yaneuraou-suisho5"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, default=default_runtime)
    parser.add_argument("--force", action="store_true", help="engine配置を作り直す")
    parser.add_argument(
        "--spsa-search-engine",
        type=Path,
        help=("ShogiArena SPSA forkからbuildしたYaneuraOu binary。指定時は探索parameter用の追加設定を生成する"),
    )
    args = parser.parse_args()

    try:
        engine_config, tournament_config, spsa_config, spsa_space = bootstrap(args.runtime_dir, force=bool(args.force))
        search_spsa = (
            prepare_search_spsa(args.runtime_dir, args.spsa_search_engine)
            if args.spsa_search_engine is not None
            else None
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("\nBootstrap complete.")
    print(f"Engine config: {engine_config}")
    print(f"Tournament config: {tournament_config}")
    print(f"SPSA config: {spsa_config}")
    print(f"SPSA space: {spsa_space}")
    print("\nRun tournament:")
    print(f'  uv run shogiarena run tournament "{tournament_config}" --dry-run')
    print(f'  uv run shogiarena run tournament "{tournament_config}"')
    print("\nRun a small SPSA demonstration:")
    print(f'  uv run shogiarena run spsa "{spsa_config}" --dry-run')
    print(f'  uv run shogiarena run spsa "{spsa_config}"')
    if search_spsa is not None:
        _search_engine_config, search_spsa_config, search_spsa_space = search_spsa
        print("\nRun the search-parameter SPSA example:")
        print(f"  Source: {SHOGIARENA_SPSA_REPOSITORY}@{SHOGIARENA_SPSA_COMMIT}")
        print(f"  SPSA space: {search_spsa_space}")
        print(f'  uv run shogiarena run spsa "{search_spsa_config}" --dry-run')
        print(f'  uv run shogiarena run spsa "{search_spsa_config}"')
    print("\nReopen the latest dashboard:")
    print(f'  uv run shogiarena dashboard serve --config "{tournament_config}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
