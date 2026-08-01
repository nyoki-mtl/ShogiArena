"""Helpers for writing dashboard HTML/CSS/JS assets."""

import hashlib
import html
import json
import shutil
import time as _time_module
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError, field_validator

from shogiarena._core.contexts.dashboard.application.live.diagnostics import load_live_diagnostics_guidelines
from shogiarena._core.contexts.game_session.ports.session_lifecycle_ports import PROFILE_KEYS, DashboardProfile
from shogiarena._core.shared.kernel.json_types import JsonValue
from shogiarena._core.shared.kernel.scalar_coercion.api import coerce_str_list

LIVE_DIAGNOSTICS_PLACEHOLDER = "__LIVE_DIAGNOSTICS_CONFIG__"
PROFILE_PLACEHOLDER = "__DASHBOARD_PROFILE__"
PROFILE_METADATA_FILENAME = ".dashboard_profiles.json"


class _ManifestEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file: str | None = None
    css: list[str] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)

    @field_validator("css", "imports", mode="before")
    @classmethod
    def _normalize_string_list(cls, value: JsonValue | None) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("must be a list")
        return coerce_str_list(value, field="manifest list entry")


class _ManifestModel(RootModel[dict[str, _ManifestEntry]]):
    pass


class _ProfileMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    profiles: list[DashboardProfile] = Field(default_factory=list)


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


def _apply_primary_profile(html_content: str, selected_profiles: tuple[DashboardProfile, ...]) -> str:
    primary_profile = selected_profiles[0]
    replacements = {
        PROFILE_PLACEHOLDER: primary_profile,
        "__LIVE_TAB_ACTIVE__": "active" if primary_profile == "tournament" else "",
        "__LIVE_TAB_SELECTED__": "true" if primary_profile == "tournament" else "false",
        "__SPSA_TAB_ACTIVE__": "active" if primary_profile == "spsa" else "",
        "__SPSA_TAB_SELECTED__": "true" if primary_profile == "spsa" else "false",
        "__MATCH_TAB_ACTIVE__": "active" if primary_profile == "match" else "",
        "__MATCH_TAB_SELECTED__": "true" if primary_profile == "match" else "false",
        "__SPRT_TAB_ACTIVE__": "active" if primary_profile == "sprt" else "",
        "__SPRT_TAB_SELECTED__": "true" if primary_profile == "sprt" else "false",
        "__GENERATE_TAB_ACTIVE__": "active" if primary_profile == "generate" else "",
        "__GENERATE_TAB_SELECTED__": "true" if primary_profile == "generate" else "false",
        "__LIVE_CONTENT_ACTIVE__": "active" if primary_profile == "tournament" else "",
        "__SPSA_CONTENT_ACTIVE__": "active" if primary_profile == "spsa" else "",
        "__MATCH_CONTENT_ACTIVE__": "active" if primary_profile == "match" else "",
        "__GENERATE_CONTENT_ACTIVE__": "active" if primary_profile == "generate" else "",
        "__SPRT_CONTENT_ACTIVE__": "active" if primary_profile == "sprt" else "",
    }
    for key, value in replacements.items():
        html_content = html_content.replace(key, value)
    return html_content


def _resolve_dashboard_asset_dirs() -> tuple[Path, Path]:
    """Resolve static/template roots for dashboard asset generation."""

    module_dir = Path(__file__).resolve().parent
    static_dir = module_dir / "static"
    template_root = module_dir / "frontend"
    if static_dir.exists() and template_root.exists():
        return static_dir, template_root

    raise FileNotFoundError("Dashboard static/frontend assets are missing under interfaces/dashboard.")


def render_dashboard_html(
    run_dir: Path,
    num_workers: int,
    *,
    api_port: int,
    profiles: Sequence[DashboardProfile] | None = None,
) -> str:
    """Render the dashboard entrypoint without writing into the run directory."""

    static_dir, template_root = _resolve_dashboard_asset_dirs()
    manifest_path = static_dir / "dist" / ".vite" / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("Dashboard build manifest not found. Run `npm run frontend:build` before packaging.")
    try:
        manifest = _ManifestModel.model_validate_json(manifest_path.read_text(encoding="utf-8")).root
    except ValidationError as exc:
        raise ValueError(f"Dashboard manifest schema is invalid: {exc}") from exc
    entry = manifest.get("src/main.ts")
    if not entry or not entry.file:
        raise KeyError("Dashboard manifest entry src/main.ts must include file")

    styles_html = "\n".join(
        f'        <link rel="stylesheet" href="static/dist/{css_file}" />' for css_file in entry.css
    )
    module_preloads = [
        f'        <link rel="modulepreload" href="static/dist/{manifest[name].file}" />'
        for name in entry.imports
        if name in manifest and manifest[name].file
    ]
    scripts_html = "\n".join(
        [*module_preloads, f'        <script type="module" src="static/dist/{entry.file}"></script>']
    )
    runtime_script = "\n".join(
        (
            f"    <script>window.ARENA_API_PORT = {api_port};",
            f"    window.__ARENA_NUM_WORKERS__ = {num_workers};",
            f"    window.__ARENA_RUN_DIR__ = {json.dumps(str(run_dir), ensure_ascii=False)};</script>",
        )
    )
    guidelines_json = json.dumps(
        load_live_diagnostics_guidelines(run_dir / "live_diagnostics.yml"),
        ensure_ascii=False,
    )
    rendered = _render_template(
        template_root / "index.html",
        runtime_script,
        styles_html + ("\n" if styles_html else ""),
        scripts_html + "\n",
        guidelines_json,
    )
    rendered = rendered.replace('<script src="data/arena_port.js"></script>', "")
    return _apply_primary_profile(rendered, _normalize_profiles(profiles))


def write_dashboard_assets(
    run_dir: Path,
    num_workers: int,
    *,
    should_overwrite_data: bool = True,
    profiles: Sequence[DashboardProfile] | None = None,
) -> None:
    """Materialize dashboard assets into ``run_dir``.

    Args:
        run_dir: Target directory for dashboard files.
        num_workers: Number of worker data files to reference.
        should_overwrite_data: When ``True`` reset data files (summary/workers). When ``False``,
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

    static_dir, template_root = _resolve_dashboard_asset_dirs()

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

    manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        manifest = _ManifestModel.model_validate(manifest_raw).root
    except ValidationError as exc:
        raise ValueError(f"Dashboard manifest schema is invalid: {exc}") from exc

    entry = manifest.get("src/main.ts")
    if not entry:
        raise KeyError("Dashboard manifest missing entry for src/main.ts")
    if not entry.file:
        raise KeyError("Dashboard manifest entry src/main.ts must include file")

    css_links = []
    for css_file in entry.css:
        css_links.append(f'        <link rel="stylesheet" href="static/dist/{css_file}" />')
    styles_html = "\n".join(css_links)
    if styles_html:
        styles_html += "\n"

    module_preloads = []
    for import_name in entry.imports:
        chunk = manifest.get(import_name)
        if not chunk:
            continue
        if not chunk.file:
            continue
        module_preloads.append(f'        <link rel="modulepreload" href="static/dist/{chunk.file}" />')
    script_tags = [
        *module_preloads,
        f'        <script type="module" src="static/dist/{entry.file}"></script>',
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

    # Generate build-meta.json for static integrity checking.
    # This is written to run_dir/static only. The builtin static metadata is
    # managed by build-time assets, not by runtime initialization.
    manifest_content = manifest_path.read_bytes()
    build_id = hashlib.sha256(manifest_content).hexdigest()[:12]
    build_meta = {
        "build_id": build_id,
        "built_at": int(_time_module.time()),
    }
    build_meta_json = json.dumps(build_meta, ensure_ascii=False)
    (static_output_dir / "build-meta.json").write_text(build_meta_json, encoding="utf-8")

    if should_overwrite_data:
        # Reset worker snapshots to empty scaffolding
        for file in workers_dir.glob("worker_*.js"):
            file.unlink(missing_ok=True)

    for i in range(num_workers):
        worker_path = workers_dir / f"worker_{i}.js"
        if should_overwrite_data or not worker_path.exists():
            worker_path.write_text(f"window.ARENA_WORKER_{i} = {{}};\n", encoding="utf-8")

    (data_dir / "arena_port.js").write_text("window.ARENA_API_PORT = 8080;\n", encoding="utf-8")


def init_dashboard_html(run_dir: Path, num_workers: int, *, profiles: Sequence[DashboardProfile] | None = None) -> None:
    """Public entry point used by arena runners to prepare dashboard files."""
    write_dashboard_assets(run_dir, num_workers, should_overwrite_data=True, profiles=profiles)


def read_dashboard_profiles_metadata(run_dir: Path) -> tuple[DashboardProfile, ...] | None:
    """Return dashboard profiles previously written for run_dir, if any.

    Args:
        run_dir: Directory where dashboard assets are stored.

    Returns:
        Tuple of profiles if metadata exists and is valid, None if file doesn't exist.

    Raises:
        json.JSONDecodeError: If metadata file exists but contains invalid JSON.
    """
    dashboard_path = run_dir / "dashboard" / PROFILE_METADATA_FILENAME
    metadata_path = dashboard_path if dashboard_path.exists() else run_dir / PROFILE_METADATA_FILENAME
    if not metadata_path.exists():
        return None

    raw_data = json.loads(metadata_path.read_text(encoding="utf-8"))
    try:
        parsed = _ProfileMetadata.model_validate(raw_data)
    except ValidationError:
        return None

    profiles: list[DashboardProfile] = []
    for item in parsed.profiles:
        if item not in profiles:
            profiles.append(item)
    if not profiles:
        return None
    return tuple(profiles)
