"""Helpers for writing dashboard HTML/CSS/JS assets."""

import html
import json
import shutil
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal, cast

from shogiarena.web.dashboard.backend.live.diagnostics import load_live_diagnostics_guidelines

LIVE_DIAGNOSTICS_PLACEHOLDER = "__LIVE_DIAGNOSTICS_CONFIG__"
PROFILE_PLACEHOLDER = "__DASHBOARD_PROFILE__"
PROFILE_METADATA_FILENAME = ".dashboard_profiles.json"
DashboardProfile = Literal["tournament", "spsa", "match", "sprt", "generate"]
PROFILE_KEYS: tuple[DashboardProfile, ...] = ("tournament", "spsa", "match", "sprt", "generate")


def _replace_block(content: str, start_marker: str, end_marker: str, replacement: str) -> str:
    """Replace the block between ``start_marker`` and ``end_marker`` with ``replacement``."""

    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)
    if start_idx == -1 or end_idx == -1 or end_idx < start_idx:
        return content
    block_start = start_idx
    block_end = end_idx + len(end_marker)
    return content[:block_start] + replacement + content[block_end:]


TEMPLATE_DEFINITIONS: tuple[
    tuple[str, tuple[str, ...], tuple[DashboardProfile, ...] | None],
    ...,
] = (
    (
        "index.html",
        ("index.html",),
        None,
    ),
)


def _render_template(
    template_path: Path,
    worker_scripts_html: str,
    styles_html: str,
    scripts_html: str,
    guidelines_json: str,
) -> str:
    html_template = template_path.read_text(encoding="utf-8")
    html_content = html_template.replace("<!-- WORKER_SCRIPTS -->", worker_scripts_html)
    html_content = html_content.replace("<!-- DASHBOARD_STYLES -->", styles_html)
    html_content = _replace_block(
        html_content,
        "<!-- DASHBOARD_SCRIPTS:start -->",
        "<!-- DASHBOARD_SCRIPTS:end -->",
        scripts_html,
    )
    return html_content.replace(
        LIVE_DIAGNOSTICS_PLACEHOLDER,
        html.escape(guidelines_json, quote=True),
    )


def _normalize_profiles(profiles: Iterable[DashboardProfile] | None) -> tuple[DashboardProfile, ...]:
    if profiles is None:
        return ("tournament", "spsa", "match", "sprt", "generate")
    normalized: list[DashboardProfile] = []
    for profile in profiles:
        if profile not in PROFILE_KEYS:
            raise ValueError(f"Unsupported dashboard profile: {profile}")
        if profile not in normalized:
            normalized.append(profile)
    if not normalized:
        raise ValueError("At least one dashboard profile must be specified")
    return tuple(normalized)


def _write_html_variants(
    run_dir: Path,
    template_root: Path,
    worker_scripts_html: str,
    styles_html: str,
    scripts_html: str,
    guidelines_json: str,
    *,
    selected_profiles: tuple[DashboardProfile, ...],
) -> None:
    profile_set = set(selected_profiles)
    primary_profile = selected_profiles[0]
    rendered_cache: dict[str, str] = {}

    def _render(relative_path: str) -> str:
        cached = rendered_cache.get(relative_path)
        if cached is not None:
            return cached
        template_path = template_root / relative_path
        if not template_path.exists():
            raise FileNotFoundError(f"Dashboard template not found: {template_path}")
        html_content = _render_template(template_path, worker_scripts_html, styles_html, scripts_html, guidelines_json)
        rendered_cache[relative_path] = html_content
        return html_content

    def _apply_profile(html_content: str) -> str:
        is_spsa = primary_profile == "spsa"
        is_match = primary_profile == "match"
        is_sprt = primary_profile == "sprt"
        is_tournament = primary_profile == "tournament"
        is_generate = primary_profile == "generate"
        replacements = {
            PROFILE_PLACEHOLDER: primary_profile,
            "__LIVE_TAB_ACTIVE__": "active" if is_tournament else "",
            "__LIVE_TAB_SELECTED__": "true" if is_tournament else "false",
            "__SPSA_TAB_ACTIVE__": "active" if is_spsa else "",
            "__SPSA_TAB_SELECTED__": "true" if is_spsa else "false",
            "__MATCH_TAB_ACTIVE__": "active" if is_match else "",
            "__MATCH_TAB_SELECTED__": "true" if is_match else "false",
            "__SPRT_TAB_ACTIVE__": "active" if is_sprt else "",
            "__SPRT_TAB_SELECTED__": "true" if is_sprt else "false",
            "__GENERATE_TAB_ACTIVE__": "active" if is_generate else "",
            "__GENERATE_TAB_SELECTED__": "true" if is_generate else "false",
            "__LIVE_CONTENT_ACTIVE__": "active" if is_tournament else "",
            "__SPSA_CONTENT_ACTIVE__": "active" if is_spsa else "",
            "__MATCH_CONTENT_ACTIVE__": "active" if is_match else "",
            "__GENERATE_CONTENT_ACTIVE__": "active" if is_generate else "",
            "__SPRT_CONTENT_ACTIVE__": "active" if is_sprt else "",
        }
        for key, value in replacements.items():
            html_content = html_content.replace(key, value)
        return html_content

    for relative_path, outputs, template_profiles in TEMPLATE_DEFINITIONS:
        if template_profiles is not None and not profile_set.intersection(template_profiles):
            continue
        html_content = _apply_profile(_render(relative_path))
        for output_name in outputs:
            target_path = run_dir / output_name
            target_path.write_text(html_content, encoding="utf-8")


def _write_profile_metadata(run_dir: Path, profiles: Sequence[DashboardProfile]) -> None:
    metadata_path = run_dir / PROFILE_METADATA_FILENAME
    payload = {"profiles": list(profiles)}
    metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_dashboard_assets(
    run_dir: Path,
    num_workers: int,
    *,
    overwrite_data: bool = True,
    profiles: Sequence[DashboardProfile] | None = None,
) -> None:
    """Materialize dashboard assets into ``run_dir``.

    Args:
        run_dir: Target directory for dashboard files.
        num_workers: Number of worker data files to reference.
        overwrite_data: When ``True`` reset data files (summary/workers). When ``False``,
            preserve existing data and only regenerate static assets/templates.
        profiles: Iterable of dashboard profiles to render (e.g. ``("tournament",)``,
            ``("spsa",)``, ``("match",)``). When omitted, all profiles are generated.
    """

    run_dir.mkdir(parents=True, exist_ok=True)

    data_dir = run_dir / "data"
    workers_dir = data_dir / "workers"
    games_dir = data_dir / "games"
    svgs_dir = data_dir / "svgs"
    for directory in (data_dir, workers_dir, games_dir, svgs_dir):
        directory.mkdir(parents=True, exist_ok=True)

    # static/ and frontend/ are in the parent dashboard/ directory
    dashboard_dir = Path(__file__).parent.parent
    static_dir = dashboard_dir / "static"
    template_root = dashboard_dir / "frontend"

    board_script = static_dir / "js" / "shared" / "shogi-board.js"
    if board_script.exists():
        shutil.copy(board_script, data_dir / "shogi-board.js")

    worker_scripts_html_lines = [f'    <script src="data/workers/worker_{i}.js"></script>' for i in range(num_workers)]
    worker_scripts_html_lines.append("    <script>window.__ARENA_NUM_WORKERS__ = " + str(num_workers) + ";</script>")
    worker_scripts_html_lines.append(
        f"    <script>window.__ARENA_RUN_DIR__ = {json.dumps(str(run_dir), ensure_ascii=False)};</script>"
    )
    worker_scripts_html = "\n".join(worker_scripts_html_lines) + "\n"

    manifest_path = static_dir / "dist" / ".vite" / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("Dashboard build manifest not found. Run `npm run frontend:build` before packaging.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest.get("src/main.ts")
    if not entry:
        raise KeyError("Dashboard manifest missing entry for src/main.ts")

    css_links = []
    for css_file in entry.get("css", []):
        css_links.append(f'        <link rel="stylesheet" href="static/dist/{css_file}" />')
    styles_html = "\n".join(css_links)
    if styles_html:
        styles_html += "\n"

    module_preloads = []
    for import_name in entry.get("imports", []) or []:
        chunk = manifest.get(import_name)
        if not chunk:
            continue
        module_preloads.append(f'        <link rel="modulepreload" href="static/dist/{chunk.get("file")}" />')
    script_tags = [
        *module_preloads,
        f'        <script type="module" src="static/dist/{entry.get("file")}"></script>',
    ]
    scripts_html = "\n".join(script_tags)
    if scripts_html:
        scripts_html += "\n"

    guidelines = load_live_diagnostics_guidelines(run_dir / "live_diagnostics.yml")
    guidelines_json = json.dumps(guidelines, ensure_ascii=False)
    selected_profiles = _normalize_profiles(profiles)
    _write_html_variants(
        run_dir,
        template_root,
        worker_scripts_html,
        styles_html,
        scripts_html,
        guidelines_json,
        selected_profiles=selected_profiles,
    )
    _write_profile_metadata(run_dir, selected_profiles)

    static_output_dir = run_dir / "static"
    if static_output_dir.exists():
        shutil.rmtree(static_output_dir)
    static_output_dir.mkdir(exist_ok=True)

    def copy_static_tree(subdirectory: str) -> None:
        source_root = static_dir / subdirectory
        if not source_root.exists():
            return

        for resource in source_root.rglob("*"):
            relative_path = resource.relative_to(source_root)
            destination = static_output_dir / subdirectory / relative_path

            if resource.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resource, destination)

    copy_static_tree("dist")

    if overwrite_data:
        # Reset worker snapshots to empty scaffolding
        for file in workers_dir.glob("worker_*.js"):
            file.unlink(missing_ok=True)

    for i in range(num_workers):
        worker_path = workers_dir / f"worker_{i}.js"
        if overwrite_data or not worker_path.exists():
            worker_path.write_text(f"window.ARENA_WORKER_{i} = {{}};\n", encoding="utf-8")

    (data_dir / "arena_port.js").write_text("window.ARENA_API_PORT = 8080;\n", encoding="utf-8")


def init_dashboard_html(run_dir: Path, num_workers: int, *, profiles: Sequence[DashboardProfile] | None = None) -> None:
    """Public entry point used by arena runners to prepare dashboard files."""
    write_dashboard_assets(run_dir, num_workers, overwrite_data=True, profiles=profiles)


def read_dashboard_profiles_metadata(run_dir: Path) -> tuple[DashboardProfile, ...] | None:
    """Return dashboard profiles previously written for run_dir, if any.

    Args:
        run_dir: Directory where dashboard assets are stored.

    Returns:
        Tuple of profiles if metadata exists and is valid, None if file doesn't exist.

    Raises:
        json.JSONDecodeError: If metadata file exists but contains invalid JSON.
    """
    metadata_path = run_dir / PROFILE_METADATA_FILENAME
    if not metadata_path.exists():
        return None

    data = json.loads(metadata_path.read_text(encoding="utf-8"))

    raw_profiles = data.get("profiles")
    if not isinstance(raw_profiles, list):
        return None

    profiles: list[DashboardProfile] = []
    for item in raw_profiles:
        if item in PROFILE_KEYS and item not in profiles:
            profiles.append(cast(DashboardProfile, item))
    if not profiles:
        return None
    return tuple(profiles)
