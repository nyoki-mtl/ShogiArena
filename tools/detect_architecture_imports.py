#!/usr/bin/env python
"""Detect import dependency violations for the modular-monolith topology."""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from textwrap import shorten

PACKAGE = "shogiarena"
INTERNAL_ROOT = "_core"
CURRENT_LEGACY_LAYER_PREFIXES = ("arena", "web", "cli")
FINAL_LEGACY_LAYER_PREFIXES = ("arena", "db", "records", "utils", "typing", "cli", "web")
LEGACY_LAYER_PROFILES: dict[str, tuple[str, ...]] = {
    "current": CURRENT_LEGACY_LAYER_PREFIXES,
    "final": FINAL_LEGACY_LAYER_PREFIXES,
}
# No interfaces subpackages are treated as legacy in the final profile.
INTERFACES_LEGACY_SUBPACKAGES: tuple[str, ...] = ()

# Canonical cross-context session substrate ports.
# These are the only `game_session/ports` modules other contexts may import directly.
SESSION_SUBSTRATE_PORT_MODULES: frozenset[str] = frozenset(
    {
        f"{PACKAGE}.{INTERNAL_ROOT}.contexts.game_session.ports.session_runtime",
    }
)

# Transitional allowlist for governed-layer imports that still target legacy modules.
# Keep this list small and delete entries as modules migrate to contexts/platform/interfaces.
LEGACY_IMPORT_ALLOWLIST: tuple[tuple[str, str], ...] = ()
# Transitional allowlist: application runtime_adapters may import from runtime layer.
# All entries cleared in 0018 and kept empty after runtime layer retirement.
CONTEXT_APPLICATION_RUNTIME_ALLOWLIST: tuple[tuple[str, str], ...] = ()
CONTEXT_ADAPTER_INTERFACES_ALLOWLIST: tuple[tuple[str, str], ...] = ()
RUNTIME_BRIDGE_MODULE = f"{PACKAGE}.{INTERNAL_ROOT}.platform.runtime_bridge"
RUNTIME_BRIDGE_EXPIRY_UTC = datetime(2026, 3, 31, 23, 59, 59, tzinfo=UTC)
RUNTIME_BRIDGE_ALLOWED_IMPORTERS: tuple[str, ...] = ()
COMPOSITION_ROOT_ALLOWED_IMPORTS: tuple[str, ...] = (
    f"{PACKAGE}.{INTERNAL_ROOT}.contexts.game_session.adapters.engine.artifact_resolver",
    f"{PACKAGE}.{INTERNAL_ROOT}.contexts.instances.adapters.engine_runtime_adapter",
    f"{PACKAGE}.{INTERNAL_ROOT}.contexts.spsa.adapters.runtime_adapter",
    f"{PACKAGE}.{INTERNAL_ROOT}.contexts.tournament.adapters.runtime_adapter",
    f"{PACKAGE}.{INTERNAL_ROOT}.contexts.dashboard.adapters.",
    f"{PACKAGE}.{INTERNAL_ROOT}.interfaces.dashboard.",
)


@dataclass(frozen=True)
class Violation:
    source_file: Path
    source_module: str
    source_layer: str
    source_line: int
    target_module: str
    target_layer: str | None
    message: str
    hint: str


def _format_layer(layer: str | None) -> str:
    return layer or "unclassified"


def build_module_index(root: Path) -> dict[str, Path]:
    package_root = root / PACKAGE
    scan_root = package_root if package_root.is_dir() else root
    module_to_file: dict[str, Path] = {}
    for file in scan_root.rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        rel = file.relative_to(scan_root)
        if rel.name == "__init__.py":
            parent_parts = rel.parent.parts
            module = f"{PACKAGE}.{'.'.join(parent_parts)}" if parent_parts else PACKAGE
        else:
            module = f"{PACKAGE}.{'.'.join(rel.with_suffix('').parts)}"
        module_to_file[module] = file
    return module_to_file


def resolve_imports(current_module: str, current_file: Path, node: ast.Import | ast.ImportFrom) -> Iterable[str]:
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name
        return

    if current_file.name == "__init__.py":
        package_parts = current_module.split(".")
    else:
        package_parts = current_module.split(".")[:-1]

    if node.level and node.level > 0:
        up_levels = node.level - 1
        if up_levels > len(package_parts):
            return
        base_parts = package_parts[: len(package_parts) - up_levels]
        if node.module is None:
            for alias in node.names:
                if alias.name == "*":
                    continue
                qualified_alias = ".".join([*base_parts, alias.name])
                if qualified_alias:
                    yield qualified_alias
            return
        module_parts = node.module.split(".")
        qualified_module = ".".join([*base_parts, *module_parts])
        if qualified_module:
            yield qualified_module
        return

    if node.module is None:
        return
    yield node.module


def map_name_to_module(name: str, module_to_file: dict[str, Path]) -> str | None:
    if not name.startswith(f"{PACKAGE}.") and name != PACKAGE:
        return None

    if name in module_to_file:
        return name

    candidates = list(module_to_file.keys())
    candidates.sort(key=len, reverse=True)
    for candidate in candidates:
        if name == candidate or name.startswith(f"{candidate}."):
            return candidate
    return None


def classify_layer(module: str, *, legacy_prefixes: tuple[str, ...], legacy_contexts_shared: bool) -> str | None:
    if module == PACKAGE:
        return None
    if not module.startswith(f"{PACKAGE}."):
        return None

    parts = module.split(".")
    if len(parts) < 2:
        return None

    if len(parts) >= 3 and parts[1] == INTERNAL_ROOT:
        parts = [parts[0], *parts[2:]]

    if parts[1] in legacy_prefixes:
        return f"legacy.{parts[1]}"

    if parts[1] == "contexts":
        if legacy_contexts_shared and len(parts) >= 3 and parts[2] == "shared":
            return "legacy.contexts.shared"
        if len(parts) >= 4 and parts[3] in {"domain", "application", "ports", "adapters"}:
            return f"contexts.{parts[2]}.{parts[3]}"
        return "contexts"

    if parts[1] == "platform":
        if len(parts) >= 3:
            return f"platform.{parts[2]}"
        return "platform"

    if parts[1] == "shared":
        if len(parts) >= 3 and parts[2] == "kernel":
            return "shared.kernel"
        return "shared"

    if parts[1] == "interfaces":
        if len(parts) >= 3 and parts[2] in INTERFACES_LEGACY_SUBPACKAGES:
            return f"legacy.{parts[2]}"
        return "interfaces"

    if parts[1] == "runtime":
        return "runtime"

    if parts[1] == "typing" and len(parts) >= 4 and parts[2] == "contracts" and parts[3] in {"parsers", "serializers"}:
        return f"typing.contracts.{parts[3]}"

    return None


def split_context(layer: str | None) -> tuple[str, str] | None:
    parts = layer.split(".") if layer else []
    if len(parts) == 3 and parts[0] == "contexts":
        return parts[1], parts[2]
    return None


def _matches_module_pattern(module: str, pattern: str) -> bool:
    if pattern.endswith("."):
        return module.startswith(pattern)
    return module == pattern or module.startswith(f"{pattern}.")


def is_legacy_import_allowlisted(source_module: str, target_module: str) -> bool:
    for source_pattern, target_pattern in LEGACY_IMPORT_ALLOWLIST:
        if _matches_module_pattern(source_module, source_pattern) and _matches_module_pattern(
            target_module, target_pattern
        ):
            return True
    return False


def is_context_adapter_interfaces_allowlisted(source_module: str, target_module: str) -> bool:
    for source_pattern, target_pattern in CONTEXT_ADAPTER_INTERFACES_ALLOWLIST:
        if _matches_module_pattern(source_module, source_pattern) and _matches_module_pattern(
            target_module, target_pattern
        ):
            return True
    return False


def is_session_substrate_port_module(target_module: str) -> bool:
    return any(_matches_module_pattern(target_module, pattern) for pattern in SESSION_SUBSTRATE_PORT_MODULES)


def is_application_runtime_allowlisted(source_module: str, target_module: str) -> bool:
    for source_pattern, target_pattern in CONTEXT_APPLICATION_RUNTIME_ALLOWLIST:
        if _matches_module_pattern(source_module, source_pattern) and _matches_module_pattern(
            target_module, target_pattern
        ):
            return True
    return False


def is_runtime_bridge_module(module: str) -> bool:
    return _matches_module_pattern(module, RUNTIME_BRIDGE_MODULE)


def is_runtime_bridge_importer_allowlisted(source_module: str) -> bool:
    return any(_matches_module_pattern(source_module, pattern) for pattern in RUNTIME_BRIDGE_ALLOWED_IMPORTERS)


def is_runtime_bridge_expired(now_utc: datetime | None = None) -> bool:
    current = now_utc or datetime.now(tz=UTC)
    return current > RUNTIME_BRIDGE_EXPIRY_UTC


def is_composition_root_allowed_import(target_module: str) -> bool:
    return any(_matches_module_pattern(target_module, pattern) for pattern in COMPOSITION_ROOT_ALLOWED_IMPORTS)


def is_governed_package_init(source_module: str, source_file: Path) -> bool:
    if source_file.name != "__init__.py":
        return False
    governed_packages = (
        f"{PACKAGE}.interfaces",
        f"{PACKAGE}.contexts",
        f"{PACKAGE}.platform",
        f"{PACKAGE}.shared",
        f"{PACKAGE}.{INTERNAL_ROOT}.interfaces",
        f"{PACKAGE}.{INTERNAL_ROOT}.contexts",
        f"{PACKAGE}.{INTERNAL_ROOT}.platform",
        f"{PACKAGE}.{INTERNAL_ROOT}.shared",
    )
    return any(source_module == package or source_module.startswith(f"{package}.") for package in governed_packages)


def is_allowed(source_layer: str, source_module: str, target_layer: str | None, target_module: str) -> bool:
    source_ctx = split_context(source_layer)
    target_ctx = split_context(target_layer)

    if is_context_adapter_interfaces_allowlisted(source_module, target_module):
        return True

    if target_layer is None:
        return True

    if target_layer.startswith("legacy."):
        return False

    if source_layer == "interfaces":
        if target_layer in {"interfaces", "shared.kernel"}:
            return True
        # Settings is a cross-cutting bootstrap concern that interfaces need.
        if target_layer == "platform.settings":
            return True
        return target_ctx is not None and target_ctx[1] in {"application", "ports"}

    if source_layer == "shared.kernel":
        return target_layer == "shared.kernel"

    if source_layer.startswith("typing.contracts."):
        if not target_module.startswith(f"{PACKAGE}."):
            return True
        if target_module.startswith(f"{PACKAGE}.typing.") or target_module.startswith(f"{PACKAGE}.utils"):
            return True
        return target_module.startswith(f"{PACKAGE}.shared")

    if source_layer == "runtime":
        if target_layer == "runtime":
            return True
        if target_layer == "shared.kernel":
            return True
        if target_layer is not None and target_layer.startswith("platform."):
            return True
        if target_layer == "interfaces":
            return True
        if target_ctx is not None:
            return True
        return False

    if source_layer.startswith("platform."):
        if target_layer == "shared.kernel":
            return True
        # Allow intra-subpackage imports (e.g. platform.engine_runtime -> platform.engine_runtime)
        if target_layer == source_layer:
            return True
        # Allow foundational platform capabilities used across platform subpackages.
        if target_layer in {"platform.settings", "platform.host_probe"}:
            return True
        return target_ctx is not None and target_ctx[1] == "ports"

    if source_layer is not None and source_ctx is not None:
        ctx, source_tier = source_ctx

        if source_tier == "domain":
            return target_layer in {f"contexts.{ctx}.domain", "shared.kernel"}

        if source_tier == "application":
            return target_layer in {
                f"contexts.{ctx}.domain",
                f"contexts.{ctx}.application",
                f"contexts.{ctx}.ports",
                "shared.kernel",
            }

        if source_tier == "ports":
            if is_session_substrate_port_module(target_module):
                return True
            return target_layer in {
                f"contexts.{ctx}.domain",
                f"contexts.{ctx}.application",
                f"contexts.{ctx}.ports",
                "shared.kernel",
            }

        if source_tier == "adapters":
            if target_layer is None:
                return False
            if target_layer == "shared.kernel":
                return True
            if target_layer.startswith("platform.") or target_ctx is not None:
                return True
            return False

    return False


def describe_allowed_layers(source_layer: str) -> str:
    if source_layer == "interfaces":
        return "interfaces, shared.kernel, platform.settings, contexts.<context>.application, contexts.<context>.ports"
    if source_layer == "shared.kernel":
        return "shared.kernel only"
    if source_layer == "runtime":
        return "runtime, shared.kernel, platform.*, contexts.*, interfaces"
    if source_layer.startswith("platform."):
        return "shared.kernel, platform.settings, platform.host_probe, contexts.<context>.ports"
    if source_layer.startswith("contexts.") and source_layer.count(".") == 2:
        context = source_layer.split(".")[1]
        _, tier = split_context(source_layer) or ("", "")
        if tier == "domain":
            return f"contexts.{context}.domain, shared.kernel"
        if tier == "application":
            return f"contexts.{context}.domain/application/ports, shared.kernel"
        if tier == "ports":
            base = f"contexts.{context}.domain/application/ports, shared.kernel"
            substrate = ", ".join(sorted(SESSION_SUBSTRATE_PORT_MODULES))
            if context == "game_session":
                return base
            return f"{base}, {substrate}"
        if tier == "adapters":
            return "platform, contexts.<context>.*, shared.kernel"
    if source_layer.startswith("typing.contracts."):
        return "typing.*, utils.*, shared.* (legacy imports disallowed)"
    return "shared.kernel"


def infer_hint(source_layer: str, target_layer: str | None, target_module: str) -> str:
    if target_layer is not None and target_layer.startswith("legacy."):
        return (
            "migrate dependency to contexts/platform/interfaces; "
            "add temporary allowlist entry only when strictly necessary"
        )
    if source_layer == "interfaces":
        if target_layer is None:
            return "move this dependency into context.application or ports"
        if target_layer.endswith(".domain"):
            return "interfaces should consume application-level DTO/service protocols"
        if target_layer.endswith(".adapters"):
            return "interfaces should target application or ports, not adapter internals"
    if source_layer == "shared.kernel":
        if target_module.startswith(f"{PACKAGE}.shared") and "kernel" not in target_module.split("."):
            return "keep shared.kernel dependency-free to avoid hidden layering"
        return "move this dependency to adapters or ports"
    if source_layer == "runtime":
        return "runtime should only depend on shared.kernel, platform, contexts, interfaces"
    if source_layer.startswith("platform."):
        return "platform should interact only via context port interfaces"
    if source_layer.startswith("contexts.") and source_layer.count(".") == 2:
        ctx, tier = split_context(source_layer) or ("", "")
        if tier == "domain":
            return f"move domain logic dependency to contexts.{ctx}.application if needed"
        if tier in {"application", "ports"}:
            return f"access {target_module} via application ports or adapter boundary"
        if tier == "adapters":
            return "prefer interfaces over direct cross-layer imports"
    if source_layer.startswith("typing.contracts."):
        return "move parser/serializer logic to adapter/interface layer"
    return "validate against dependency_governance.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check architecture import dependencies.")
    parser.add_argument("--root", default="src", help="Project root directory to scan for Python imports")
    parser.add_argument(
        "--fail-on-violations",
        action="store_true",
        help="Return non-zero on violations.",
    )
    parser.add_argument(
        "--legacy-profile",
        choices=tuple(LEGACY_LAYER_PROFILES.keys()),
        default="current",
        help=(
            "Legacy import profile. "
            "'current': arena/web/cli. "
            "'final': arena/db/records/utils/typing/cli/web + contexts.shared."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root)
    if not root.exists():
        raise SystemExit(f"target root does not exist: {root}")

    legacy_prefixes = LEGACY_LAYER_PROFILES[args.legacy_profile]
    legacy_contexts_shared = args.legacy_profile == "final"

    def classify(module: str) -> str | None:
        return classify_layer(
            module,
            legacy_prefixes=legacy_prefixes,
            legacy_contexts_shared=legacy_contexts_shared,
        )

    module_to_file = build_module_index(root)
    violations: list[Violation] = []

    for source_module, source_file in module_to_file.items():
        if any(part in source_module for part in {"__pycache__", ".tox"}):
            continue

        try:
            tree = ast.parse(source_file.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue

        source_layer = classify(source_module)
        is_package_root_init = source_module == PACKAGE and source_file.name == "__init__.py"
        if source_layer is None and not is_package_root_init:
            continue
        if source_layer is not None and source_layer.startswith("legacy."):
            continue
        governed_init = is_governed_package_init(source_module, source_file)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue

            for raw_name in resolve_imports(source_module, source_file, node):
                if governed_init and raw_name.startswith(f"{PACKAGE}.") and raw_name != source_module:
                    target_module = map_name_to_module(raw_name, module_to_file) or raw_name
                    violations.append(
                        Violation(
                            source_file=source_file.relative_to(root),
                            source_module=source_module,
                            source_layer=source_layer,
                            source_line=node.lineno,
                            target_module=target_module,
                            target_layer=classify(target_module),
                            message=f"runtime import in package __init__: {raw_name}",
                            hint="avoid package-level runtime re-export; import concrete module at call sites",
                        )
                    )
                    continue

                target_module = map_name_to_module(raw_name, module_to_file)
                if target_module is None:
                    continue
                if target_module == source_module:
                    continue
                if target_module == PACKAGE:
                    continue

                if is_package_root_init:
                    if target_module.startswith(f"{PACKAGE}.") and not is_composition_root_allowed_import(
                        target_module
                    ):
                        violations.append(
                            Violation(
                                source_file=source_file.relative_to(root),
                                source_module=source_module,
                                source_layer="composition_root",
                                source_line=node.lineno,
                                target_module=target_module,
                                target_layer=classify(target_module),
                                message=f"import {raw_name} (composition root import not allowlisted)",
                                hint=(
                                    "composition root may import only allowlisted adapter modules for dependency wiring"
                                ),
                            )
                        )
                    continue

                target_layer = classify(target_module)
                if is_runtime_bridge_module(target_module):
                    if not is_runtime_bridge_importer_allowlisted(source_module):
                        violations.append(
                            Violation(
                                source_file=source_file.relative_to(root),
                                source_module=source_module,
                                source_layer=source_layer,
                                source_line=node.lineno,
                                target_module=target_module,
                                target_layer=target_layer,
                                message=f"import {raw_name} (platform.runtime_bridge importer not allowlisted)",
                                hint=(
                                    "platform.runtime_bridge is transitional; only the explicit 0016 allowlist "
                                    "may import it"
                                ),
                            )
                        )
                        continue
                    if is_runtime_bridge_expired():
                        violations.append(
                            Violation(
                                source_file=source_file.relative_to(root),
                                source_module=source_module,
                                source_layer=source_layer,
                                source_line=node.lineno,
                                target_module=target_module,
                                target_layer=target_layer,
                                message=(
                                    f"import {raw_name} (platform.runtime_bridge expiry exceeded: 2026-03-31T23:59:59Z)"
                                ),
                                hint=(
                                    "remove runtime_bridge dependency or formalize permanent policy in "
                                    "dependency_governance.md + decisions.md with same-slice lint update"
                                ),
                            )
                        )
                        continue
                if (
                    target_layer is not None
                    and target_layer.startswith("legacy.")
                    and is_legacy_import_allowlisted(source_module, target_module)
                ):
                    continue
                if is_application_runtime_allowlisted(source_module, target_module):
                    continue
                if (
                    source_layer == "interfaces"
                    and target_layer is None
                    and target_module.startswith(f"{PACKAGE}.web.")
                ):
                    violations.append(
                        Violation(
                            source_file=source_file.relative_to(root),
                            source_module=source_module,
                            source_layer=source_layer,
                            source_line=node.lineno,
                            target_module=target_module,
                            target_layer=target_layer,
                            message=f"import {raw_name}",
                            hint="interfaces should not depend on web/* directly; route via interface/backend adapters",
                        )
                    )
                    continue

                # Composition root is the single place that wires adapters to ports.
                if source_module.startswith(
                    f"{PACKAGE}.{INTERNAL_ROOT}.interfaces.composition_root."
                ) and is_composition_root_allowed_import(target_module):
                    continue

                if is_allowed(source_layer, source_module, target_layer, target_module):
                    continue

                violations.append(
                    Violation(
                        source_file=source_file.relative_to(root),
                        source_module=source_module,
                        source_layer=source_layer,
                        source_line=node.lineno,
                        target_module=target_module,
                        target_layer=target_layer,
                        message=f"import {raw_name}",
                        hint=infer_hint(source_layer, target_layer, target_module),
                    )
                )

    if not violations:
        print("Architecture import check: no violations")
        return 0

    print("Architecture import violations found:")
    legacy_scope_parts = [*legacy_prefixes]
    if legacy_contexts_shared:
        legacy_scope_parts.append("contexts.shared")
    legacy_scope = "/".join(legacy_scope_parts)
    print(
        "Rules: interfaces -> application|ports|shared.kernel; "
        "interfaces -> direct web/* imports forbidden; "
        f"governed layers -> legacy {legacy_scope} imports forbidden (except temporary allowlist); "
        "context/domain -> domain|shared.kernel; "
        "context/application -> domain|application|ports|shared.kernel; "
        "context/ports -> domain|application|ports|shared.kernel|contexts.game_session.ports.session_runtime; "
        "context/adapters -> platform|ports|shared.kernel|other context; "
        "platform -> ports|shared.kernel; "
        "typing/contracts -> shared or allowed utility packages; "
        "governed package __init__.py -> no runtime re-export imports."
    )
    for violation in violations:
        # Report POSIX-style paths so output is identical on Windows and Linux.
        print(
            f"{violation.source_file.as_posix()}:{violation.source_line} "
            f"{violation.source_layer} -> {_format_layer(violation.target_layer)}: "
            f"{violation.message} ({violation.target_module}); "
            f"allowed: {describe_allowed_layers(violation.source_layer)}; "
            f"hint: {shorten(violation.hint, width=140, placeholder='...')}"
        )

    if args.fail_on_violations:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
